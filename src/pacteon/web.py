"""
Pacteon Should-Cost Calculator — Web interface.

Run with:
    uvicorn pacteon.web:app --reload --port 8000
"""
import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

load_dotenv()

app = FastAPI(title="Pacteon Should-Cost Calculator")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# In-memory job store — fine for single-user demo
_jobs: dict[str, dict] = {}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/analyze/upload")
async def upload(file: UploadFile = File(...), quantity: int = Form(1)):
    job_id = uuid.uuid4().hex[:10]
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        _jobs[job_id] = {"path": tmp.name, "quantity": quantity, "filename": file.filename}
    return {"job_id": job_id}


@app.get("/analyze/stream/{job_id}")
async def stream(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    return StreamingResponse(
        _pipeline_stream(job_id, job["path"], job["filename"], job["quantity"]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _pipeline_stream(job_id: str, tmp_path: str, filename: str, quantity: int):
    import anthropic

    from .agents import DrawingReaderAgent, ShopForemanAgent
    from .models.quote import LineItem, Quote
    from .tools.cost_calculator import calc_material_cost

    def sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    def log(text: str, style: str = "info"):
        return sse({"type": "log", "style": style, "text": text})

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        # ── Stage 1 ──────────────────────────────────────────────────────────
        yield log(f"Stage 1: Reading engineering drawing... [{filename}]", "stage")

        reader = DrawingReaderAgent(client)

        _stage1_steps = [
            (0.6,  "  Rendering PDF pages as high-res images..."),
            (2.5,  "  Extracting embedded text layer..."),
            (4.5,  "  Sending to WonderVision..."),
            (9.0,  "  Analyzing orthographic views (top, front, side)..."),
            (15.0, "  Reading title block and bill of materials..."),
            (21.0, "  Identifying holes, slots, and edge features..."),
            (28.0, "  Parsing weld symbols and GD&T callouts..."),
            (36.0, "  Resolving flat blank geometry..."),
        ]

        task = asyncio.ensure_future(asyncio.to_thread(reader.read, tmp_path))
        loop = asyncio.get_running_loop()
        t0   = loop.time()
        step_idx = 0

        while not task.done():
            elapsed = loop.time() - t0
            while step_idx < len(_stage1_steps) and elapsed >= _stage1_steps[step_idx][0]:
                yield log(_stage1_steps[step_idx][1], "dim")
                step_idx += 1
            await asyncio.sleep(0.3)

        drawing = await task

        dims = f'{drawing.length_in}" × {drawing.width_in}"'
        if drawing.is_formed:
            dims += f' blank, formed height {drawing.formed_height_in}"'

        yield log(f"  Part:     {drawing.part_name or drawing.part_number}", "info")
        yield log(f"  Material: {drawing.material} ({dims})", "info")
        yield log(f"  Features: {len(drawing.features)} found", "info")
        for f in drawing.features:
            yield log(f"    [{f.zone}] {f.quantity}× {f.feature_type} — {f.description}", "dim")

        # ── Stage 2 ──────────────────────────────────────────────────────────
        yield log("", "info")
        yield log("Stage 2: Determining vendor processes...", "stage")

        foreman = ShopForemanAgent(client)
        processes = await asyncio.to_thread(foreman.assign_routes, drawing)

        for p in processes:
            yield log(
                f"  [{p.feature_zone}] {p.machine_type}: {p.feature_description}"
                f"  →  {p.estimated_time_hr:.3f} hr  →  ${p.labor_cost:.2f}",
                "success",
            )

        # ── Stage 3 ──────────────────────────────────────────────────────────
        yield log("", "info")
        yield log("Stage 3: Calculating costs...", "stage")

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
            description=f'Raw material: {drawing.material} ({drawing.length_in}" × {drawing.width_in}")',
            quantity=quantity,
            unit="pcs",
            unit_price=material_unit,
            total=material_total,
        ))

        for proc in processes:
            quote.labor_cost += proc.labor_cost * quantity
            quote.machine_cost += (proc.total_cost - proc.labor_cost) * quantity
            quote.line_items.append(LineItem(
                description=f"{proc.machine_type} — {proc.feature_description} (zone {proc.feature_zone})",
                quantity=quantity,
                unit="pcs",
                unit_price=proc.total_cost,
                total=proc.total_cost * quantity,
            ))
            quote.routing_steps.append(f"{proc.machine_type} / {proc.tool_used}: {proc.feature_description}")

        summary = quote.summary()
        subtotal = summary["material_cost"] + summary["labor_cost"] + summary["machine_cost"]

        yield log("", "info")
        yield log(f"  Material:    ${material_total:.2f}", "info")
        yield log(f"  Processing:  ${(summary['labor_cost'] + summary['machine_cost']):.2f}", "info")
        yield log(f"  Overhead:    ${summary['overhead']:.2f}", "info")
        yield log(f"  Margin:      ${summary['margin']:.2f}", "info")
        yield log(f"  {'─' * 24}", "dim")
        yield log(f"  TOTAL:       ${summary['total_price']:.2f}", "total")

        yield sse({
            "type": "result",
            "data": {
                "part": {
                    "number": drawing.part_number or "—",
                    "name": drawing.part_name or "—",
                    "revision": drawing.revision or "—",
                    "material": drawing.material or "—",
                    "dimensions": f'{drawing.length_in}" × {drawing.width_in}"',
                    "form_type": drawing.part_form_type or "—",
                    "formed_height": drawing.formed_height_in,
                },
                "line_items": [
                    {
                        "description": item.description,
                        "quantity": item.quantity,
                        "unit_price": round(item.unit_price, 2),
                        "total": round(item.total, 2),
                    }
                    for item in quote.line_items
                ],
                "summary": {
                    "subtotal": round(subtotal, 2),
                    "overhead": round(summary["overhead"], 2),
                    "margin": round(summary["margin"], 2),
                    "total": round(summary["total_price"], 2),
                },
                "routing": quote.routing_steps,
            },
        })

        yield sse({"type": "done"})

    except Exception as e:
        yield log(f"  Error: {e}", "error")
        yield sse({"type": "error", "message": str(e)})

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        _jobs.pop(job_id, None)
