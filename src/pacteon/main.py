"""
Pacteon Should-Cost Calculator — Main pipeline entry point.

Usage:
    python -m pacteon.main path/to/drawing.pdf
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import anthropic
from rich.console import Console
from rich.table import Table

from .agents import DrawingReaderAgent, ShopForemanAgent
from .models.quote import LineItem, Quote
from .tools.cost_calculator import calc_material_cost, calc_machine_cost

load_dotenv()
console = Console()


def run_pipeline(pdf_path: str | Path, quantity: int = 1) -> Quote:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Stage 1: Read the drawing
    console.print("[bold cyan]Stage 1:[/bold cyan] Reading engineering drawing...")
    reader = DrawingReaderAgent(client)
    drawing = reader.read(pdf_path)
    console.print(f"  Part: {drawing.part_name or drawing.part_number}")
    console.print(f"  Material: {drawing.material} ({drawing.length_in}\" x {drawing.width_in}\")")
    console.print(f"  Features found: {len(drawing.features)}")

    # Stage 2: Shop foreman assigns machine routes
    console.print("\n[bold cyan]Stage 2:[/bold cyan] Shop foreman routing features...")
    foreman = ShopForemanAgent(client)
    processes = foreman.assign_routes(drawing)

    # Stage 3: Calculate costs deterministically
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
    console.print(f"\n  Subtotal:  ${summary['material_cost'] + summary['labor_cost'] + summary['machine_cost']:.2f}")
    console.print(f"  Overhead:  ${summary['overhead']:.2f}")
    console.print(f"  Margin:    ${summary['margin']:.2f}")
    console.print(f"[bold]  TOTAL:     ${summary['total_price']:.2f}[/bold]")
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
