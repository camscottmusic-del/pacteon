"""
Pacteon Should-Cost Calculator — Main pipeline entry point.

Usage:
    python -m pacteon.main path/to/drawing.pdf
"""
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
import anthropic
from rich.console import Console
from rich.table import Table

from .agents import DrawingReaderAgent, ShopForemanAgent
from .agents.eng_info_specialist import EngInfoSpecialistAgent
from .agents.specialist_dispatcher import SpecialistDispatcher
from .models.quote import LineItem, Quote
from .tools.cost_calculator import calc_material_cost, calc_machine_cost

load_dotenv()
console = Console()

_QUOTES_LOG = Path(__file__).parents[2] / "data" / "quotes.jsonl"


def _log_run(drawing, quote) -> None:
    """Append a run record to data/quotes.jsonl for the calibration loop."""
    import json as _json
    from datetime import datetime, timezone
    record = {
        "run_id": f"r-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{drawing.part_number or 'unknown'}",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "part_number": drawing.part_number,
        "part_name": drawing.part_name,
        "drawing": {
            "material": drawing.material,
            "material_key": drawing.material_key,
            "part_form_type": drawing.part_form_type,
            "length_in": drawing.length_in,
            "width_in": drawing.width_in,
            "thickness_in": drawing.thickness_in,
            "feature_count": len(drawing.features),
        },
        "quote": {
            "total_price": round(quote.total_price, 2),
            "material_cost": round(quote.material_cost, 2),
            "labor_cost": round(quote.labor_cost, 2),
            "machine_cost": round(quote.machine_cost, 2),
            "specialist_correction": round(quote.specialist_correction, 2),
            "pipeline_elapsed_sec": quote.pipeline_elapsed_sec,
            "line_items": [
                {"description": li.description, "total": round(li.total, 2)}
                for li in quote.line_items
            ],
        },
        "po_actual_price": None,
        "delta_pct": None,
    }
    try:
        _QUOTES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_QUOTES_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record) + "\n")
    except Exception:
        pass  # logging failure must never break the pipeline


def _elapsed(t: float) -> str:
    return f"({time.time() - t:.1f}s)"


def run_pipeline(pdf_path: str | Path, quantity: int = 1) -> Quote:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    t_pipeline = time.time()

    # Stage 1: Read the drawing
    t_stage = time.time()
    console.print(f"[bold cyan]Stage 1:[/bold cyan] Reading engineering drawing... [{Path(pdf_path).name}]")
    reader = DrawingReaderAgent(client)
    drawing = reader.read(pdf_path)
    console.print(f"  Part: {drawing.part_name or drawing.part_number}")
    dims = f"{drawing.length_in}\" x {drawing.width_in}\""
    if drawing.is_formed:
        dims += f" blank, formed height {drawing.formed_height_in}\""
    console.print(f"  Material: {drawing.material} ({dims})")
    console.print(f"  Features found: {len(drawing.features)}")
    for f in drawing.features:
        console.print(f"    [{f.zone}] {f.quantity}x {f.feature_type} -- {f.description}")
    console.print(f"  [dim]Stage 1 complete {_elapsed(t_stage)}[/dim]")

    # Stage 1.5: Engineering standards validation
    t_stage = time.time()
    console.print("\n[bold cyan]Stage 1.5:[/bold cyan] Validating against ASME/ASTM/AWS standards...")
    try:
        eng_info = EngInfoSpecialistAgent(client)
        drawing, validation = eng_info.validate(drawing)
        if validation.has_errors:
            for msg in validation.error_messages:
                console.print(f"  [bold red][STANDARDS ERROR][/bold red] {msg}")
            console.print("[bold red]  Halting -- fix drawing interpretation errors above before routing.[/bold red]")
            raise ValueError(f"Standards validation errors: {'; '.join(validation.error_messages)}")
        for note in validation.warning_notes:
            console.print(f"  [yellow]{note}[/yellow]")
        if validation.standards_gaps:
            for gap in validation.standards_gaps:
                console.print(f"  [dim][STANDARDS GAP] {gap['type']}: {gap['value']} -- {gap['suggested_addition']}[/dim]")
        if not validation.flags and not validation.feature_corrections:
            console.print("  Drawing validated -- no standards issues found.")
        else:
            console.print(f"  Validated: {len(validation.feature_corrections)} correction(s), {len(validation.flags)} flag(s)")
        console.print(f"  [dim]Stage 1.5 complete {_elapsed(t_stage)}[/dim]")
    except ValueError:
        raise
    except Exception as e:
        console.print(f"  [yellow]Stage 1.5 skipped (non-fatal): {e}[/yellow]")

    # Stage 2: Determine vendor processes + calculate times deterministically
    t_stage = time.time()
    console.print("\n[bold cyan]Stage 2:[/bold cyan] Determining vendor processes...")
    foreman = ShopForemanAgent(client)
    try:
        processes = foreman.assign_routes(drawing)
        for p in processes:
            console.print(
                f"  [{p.feature_zone}] {p.machine_type}: {p.feature_description}"
                f" -> {p.estimated_time_hr:.3f} hr -> ${p.total_cost:.2f}"
            )
        console.print(f"  [dim]Stage 2 complete {_elapsed(t_stage)}[/dim]")
    except Exception as e:
        import traceback
        console.print(f"[bold red]Stage 2 error:[/bold red] {e}")
        traceback.print_exc()
        processes = []

    # Stage 2.5: Specialist parameter review
    specialist_correction = 0.0
    if processes:
        t_stage = time.time()
        foreman_process_total = sum(p.total_cost for p in processes)
        console.print("\n[bold cyan]Stage 2.5:[/bold cyan] Specialist parameter review...")
        try:
            dispatcher = SpecialistDispatcher(client)
            processes = dispatcher.review(drawing, processes)
            specialist_process_total = sum(p.total_cost for p in processes)
            specialist_correction = foreman_process_total - specialist_process_total
            reviewed_count = sum(1 for p in processes if p.specialist_reviewed)
            console.print(f"  {reviewed_count}/{len(processes)} assignments specialist-reviewed")
            for p in processes:
                if p.notes and ("specialist]" in p.notes or "eng-doc]" in p.notes):
                    console.print(f"  [{p.feature_zone}] {p.machine_type}: {p.notes}", style="dim")
            if specialist_correction > 0.01:
                console.print(f"  [bold green]Potential overcharge caught: ${specialist_correction:.2f}[/bold green]  (foreman ${foreman_process_total:.2f} -> specialist ${specialist_process_total:.2f})")
            elif specialist_correction < -0.01:
                console.print(f"  [yellow]Underestimate corrected: +${abs(specialist_correction):.2f}[/yellow]  (foreman ${foreman_process_total:.2f} -> specialist ${specialist_process_total:.2f})")
            else:
                console.print(f"  Parameters confirmed -- no cost adjustment (${foreman_process_total:.2f})")
            console.print(f"  [dim]Stage 2.5 complete {_elapsed(t_stage)}[/dim]")
        except Exception as e:
            console.print(f"  [yellow]Stage 2.5 skipped (non-fatal): {e}[/yellow]")

    # Stage 3: Calculate costs deterministically
    t_stage = time.time()
    console.print("\n[bold cyan]Stage 3:[/bold cyan] Calculating costs...")
    material_unit, material_total = calc_material_cost(
        drawing.material_key or "A36_STEEL",
        drawing.length_in,
        drawing.width_in,
        quantity,
    )

    quote = Quote(
        part_number=drawing.part_number,
        part_name=drawing.part_name,
        revision=drawing.revision,
        material_cost=material_total,
        specialist_correction=round(specialist_correction, 2),
    )

    quote.line_items.append(LineItem(
        description=f"Raw material: {drawing.material} ({drawing.length_in}\" x {drawing.width_in}\")",
        quantity=quantity,
        unit="pcs",
        unit_price=material_unit,
        total=material_total,
    ))

    for proc in processes:
        op_cost = proc.total_cost * quantity
        quote.labor_cost += proc.labor_cost * quantity
        quote.machine_cost += (proc.total_cost - proc.labor_cost) * quantity
        quote.line_items.append(LineItem(
            description=f"{proc.machine_type} — {proc.feature_description} (zone {proc.feature_zone})",
            quantity=quantity,
            unit="pcs",
            unit_price=proc.total_cost,
            total=op_cost,
        ))
        quote.routing_steps.append(
            f"{proc.machine_type} / {proc.tool_used}: {proc.feature_description}"
        )

    elapsed = round(time.time() - t_pipeline, 1)
    quote.pipeline_elapsed_sec = elapsed
    console.print(f"  [dim]Stage 3 complete {_elapsed(t_stage)}[/dim]")
    console.print(f"\n  [dim]Total pipeline time: {elapsed}s[/dim]")
    _log_run(drawing, quote)
    return quote


def print_quote(quote: Quote) -> None:
    console.print("\n[bold green]--- SHOULD-COST ESTIMATE ---[/bold green]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Description")
    table.add_column("Qty", justify="right")
    table.add_column("Unit $", justify="right")
    table.add_column("Total $", justify="right")

    for item in quote.line_items:
        table.add_row(item.description, str(item.quantity), f"{item.unit_price:.2f}", f"{item.total:.2f}")

    console.print(table)
    summary = quote.summary()
    subtotal = summary['material_cost'] + summary['labor_cost'] + summary['machine_cost']
    console.print(f"\n  Subtotal:  ${subtotal:.2f}")
    console.print(f"  Overhead:  ${summary['overhead']:.2f}")
    console.print(f"  Margin:    ${summary['margin']:.2f}")
    console.print(f"[bold]  TOTAL:     ${summary['total_price']:.2f}[/bold]")

    if abs(quote.specialist_correction) > 0.01:
        if quote.specialist_correction > 0:
            console.print(
                f"\n[bold green]  Specialist review caught ${quote.specialist_correction:.2f} in potential overcharges[/bold green]"
            )
        else:
            console.print(
                f"\n[yellow]  Specialist review corrected +${abs(quote.specialist_correction):.2f} underestimate (more accurate)[/yellow]"
            )
    else:
        console.print("\n  [dim]Specialist review: no cost adjustment[/dim]")

    if quote.pipeline_elapsed_sec:
        console.print(f"  [dim]Pipeline completed in {quote.pipeline_elapsed_sec}s[/dim]")

    console.print("\n[bold]Machine Routing (for ERP upload):[/bold]")
    for i, step in enumerate(quote.routing_steps, 1):
        console.print(f"  {i:02d}. {step}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("[red]Usage: python -m pacteon.main <drawing.pdf> [quantity][/red]")
        sys.exit(1)
    pdf = sys.argv[1]
    qty = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    result = run_pipeline(pdf, qty)
    print_quote(result)
