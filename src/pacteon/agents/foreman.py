"""
ShopForemanAgent — Stage 2 of the pipeline.

Determines what vendor processes are required to manufacture a complete part.
AI assigns features to process IDs; process_calculator computes time and cost
deterministically from process_library.json.
"""
import json
from pathlib import Path

import anthropic

from ..models.drawing import ExtractedDrawing
from ..models.machine import MachineProcess
from ..tools.process_calculator import calc_process_time, calc_process_cost, calc_setup_time

_VENDORS_PATH = Path(__file__).parents[3] / "data" / "vendor_processes.json"

_SYSTEM_PROMPT = """\
You are a manufacturing cost estimator. Your job is to determine what vendor processes
are required to manufacture a part completely, given its drawing features and form type.

You are NOT routing through a factory floor. You are answering:
"If I sent this drawing to a vendor, what processes would they need to perform to deliver a finished part?"

## Routing rules by form_type

- **plate**: Profile cut on LASER_CUT or WATERJET. Secondary holes/taps → TAP or CNC_MILL.
- **flat_stock**: All ops on CNC_MILL.
- **tube**: Primary cut on TUBE_LASER. Secondary ops → CNC_MILL if needed.
- **round_bar**: Primary turning on LATHE. Secondary milling → CNC_MILL.
- **weldment**: Each component by its own form_type, then WELD_TIG for assembly.

### sheet_metal — route depends on whether the part is formed

- **sheet_metal, NOT formed (is_formed = false, no bend features)**:
  LASER_CUT for the flat profile only. TAP for any threaded holes. Finish if called out.
  Do NOT assign PRESS_BRAKE.

- **sheet_metal, IS formed (is_formed = true, has bend features)**:
  LASER_CUT for the flat blank profile → PRESS_BRAKE (one assignment, quantity = total bend count)
  → TAP for threaded holes (done post-forming) → WELD_TIG only if weld symbols are explicitly present
  → Finish (PAINT, POWDER_COAT, or ANODIZE) only if explicitly called out.

## Rules
- Every feature must map to exactly one process_id.
- For geometry-based processes (LASER_CUT, WATERJET, TUBE_LASER): provide cut_length_in
  (estimate the cut perimeter from blank dimensions if not explicit on the drawing) and
  pierce_count (total number of holes + slots that require a laser pierce).
- For count-based processes (TAP, CNC_MILL, LATHE, WELD_TIG): provide
  quantity (number of operations — tapped holes, welds, etc.).
- For PRESS_BRAKE: provide quantity (number of bends) AND cut_length_in (the bend chord
  length in inches — typically the longer blank dimension for sheet metal). A 48" panel
  takes far more time to brake than a 4" bracket; the length drives positioning and
  handling time.
- For finish processes (PAINT, POWDER_COAT, ANODIZE): quantity = 1.
- Only include finish operations if explicitly called out in drawing notes or finish spec.
- Do NOT invent PRESS_BRAKE assignments on flat parts with no bend features.
- Do NOT include estimated_time_hr — time is calculated deterministically by the system.

Always call assign_vendor_processes with your complete assignments.
"""

_ASSIGN_TOOL: anthropic.types.ToolParam = {
    "name": "assign_vendor_processes",
    "description": "Map each drawing feature to the vendor process required to produce it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "feature_zone":        {"type": "string"},
                        "feature_description": {"type": "string"},
                        "process_id":          {"type": "string", "description": "Must match a key in vendor_processes.json"},
                        "quantity":            {"type": "integer", "description": "Number of operations (bends, taps, welds, etc.)"},
                        "cut_length_in":       {"type": "number",  "description": "Estimated cut perimeter in inches (geometry processes only)"},
                        "pierce_count":        {"type": "integer", "description": "Number of pierce points / holes (geometry processes only)"},
                        "notes":               {"type": "string"},
                    },
                    "required": ["feature_zone", "feature_description", "process_id", "quantity"],
                },
            }
        },
        "required": ["assignments"],
    },
}


class ShopForemanAgent:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client
        self._vendors = json.loads(_VENDORS_PATH.read_text())

    def assign_routes(self, drawing: ExtractedDrawing) -> list[MachineProcess]:
        """Assign vendor processes to every feature and calculate costs deterministically."""
        features_text = "\n".join(
            f"- Zone {f.zone}: {f.quantity}x {f.feature_type} — {f.description}"
            + (f" [{f.dimension}]" if f.dimension else "")
            for f in drawing.features
        ) or "(no explicit features — route based on form_type and blank dimensions)"

        prompt = (
            f"Part: {drawing.part_name or drawing.part_number or 'Unknown'}\n"
            f"Form type: {drawing.part_form_type}\n"
            f"Material: {drawing.material} ({drawing.material_key or 'unknown key'})\n"
            f"Blank: {drawing.length_in}\" × {drawing.width_in}\""
            + (f", formed height {drawing.formed_height_in}\"" if drawing.is_formed else "") + "\n\n"
            f"Features:\n{features_text}\n\n"
            f"Available processes: {', '.join(self._vendors.keys())}"
        )

        response = self.client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            tools=[_ASSIGN_TOOL],
            tool_choice={"type": "tool", "name": "assign_vendor_processes"},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_block = next(b for b in response.content if b.type == "tool_use")
        assignments = tool_block.input["assignments"]

        blank_area = drawing.length_in * drawing.width_in
        processes = []

        for a in assignments:
            process_id = a["process_id"]
            if process_id not in self._vendors:
                continue  # skip if AI hallucinated an unknown process

            vendor_proc = self._vendors[process_id]
            rate = vendor_proc["rate_per_hr"]

            quantity    = a.get("quantity", 1)
            pierce_count = a.get("pierce_count", 0)

            run_time_hr = calc_process_time(
                process_id=process_id,
                quantity=quantity,
                material_key=drawing.material_key or "DEFAULT",
                thickness_in=drawing.thickness_in,
                blank_area_sq_in=blank_area,
                cut_length_in=a.get("cut_length_in", 0.0),
                pierce_count=pierce_count,
            )
            setup_time_hr = calc_setup_time(
                process_id=process_id,
                quantity=quantity,
                pierce_count=pierce_count,
            )
            setup_cost, run_cost = calc_process_cost(process_id, run_time_hr, setup_time_hr)
            labor_cost = setup_cost + run_cost

            processes.append(MachineProcess(
                feature_zone=a["feature_zone"],
                feature_description=a["feature_description"],
                machine_id=process_id,
                machine_type=vendor_proc["name"],
                tool_used=process_id,
                estimated_time_hr=run_time_hr,
                setup_time_hr=setup_time_hr,
                rate_per_hr=rate,
                labor_cost=labor_cost,
                notes=a.get("notes"),
            ))

        return processes
