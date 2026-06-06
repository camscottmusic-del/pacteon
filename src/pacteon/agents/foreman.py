"""
ShopForemanAgent — Stage 2 of the pipeline.

Acts as the AI shop foreman: takes the extracted drawing features and the
available machine inventory, then assigns each feature to the optimal machine
and estimates run time. Returns a list of MachineProcess records.
"""
import json
from pathlib import Path

import anthropic

from ..models.drawing import ExtractedDrawing
from ..models.machine import MachineProcess

_MACHINES_PATH = Path(__file__).parents[4] / "data" / "machines.json"

_SYSTEM_PROMPT = """\
You are an experienced manufacturing shop foreman at Schneider Packaging, a precision fabrication facility.

You will be given:
1. A list of manufacturing features extracted from an engineering drawing (zone, type, quantity, dimensions).
2. The available machine inventory with their capabilities and hourly rates.

Your job is to assign each feature to the best available machine and estimate the run time in hours.
Think about: setup efficiency (batching similar operations), material flow through the shop, machine capabilities.

Rules:
- Every feature must be assigned to exactly one machine.
- Estimate run_time_hr conservatively — it is better to slightly overestimate than underestimate.
- If a feature could use multiple machines, pick the most cost-efficient one.
- Small holes < 0.5" diameter: prefer the laser if material thickness allows, else CNC mill.
- Welds: always assign to WELDER_TIG_1 unless noted otherwise.
- Bends/forms: always assign to PRESS_BRAKE_1.

Always call assign_machine_routes with your full assignments.
"""

_ASSIGN_TOOL: anthropic.types.ToolParam = {
    "name": "assign_machine_routes",
    "description": "Assign each drawing feature to a machine and estimate run time.",
    "input_schema": {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "feature_zone": {"type": "string"},
                        "feature_description": {"type": "string"},
                        "machine_id": {"type": "string", "description": "Must match a key in the machines inventory"},
                        "machine_type": {"type": "string"},
                        "tool_used": {"type": "string"},
                        "estimated_time_hr": {"type": "number"},
                        "setup_time_hr": {"type": "number"},
                        "notes": {"type": "string"},
                    },
                    "required": [
                        "feature_zone", "feature_description", "machine_id",
                        "machine_type", "tool_used", "estimated_time_hr", "setup_time_hr",
                    ],
                },
            }
        },
        "required": ["assignments"],
    },
}


class ShopForemanAgent:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client
        self._machines = json.loads(_MACHINES_PATH.read_text())["machines"]

    def assign_routes(self, drawing: ExtractedDrawing) -> list[MachineProcess]:
        """Assign machine routes to every feature in the extracted drawing."""
        features_text = "\n".join(
            f"- Zone {f.zone}: {f.quantity}x {f.feature_type} — {f.description}"
            + (f" [{f.dimension}]" if f.dimension else "")
            for f in drawing.features
        )
        machines_text = json.dumps(self._machines, indent=2)

        prompt = (
            f"Part: {drawing.part_name or drawing.part_number or 'Unknown'}\n"
            f"Material: {drawing.material}\n\n"
            f"Features to route:\n{features_text}\n\n"
            f"Available machines:\n{machines_text}"
        )

        response = self.client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            tools=[_ASSIGN_TOOL],
            tool_choice={"type": "tool", "name": "assign_machine_routes"},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_block = next(b for b in response.content if b.type == "tool_use")
        assignments = tool_block.input["assignments"]

        processes = []
        for a in assignments:
            machine = self._machines.get(a["machine_id"], {})
            rate = machine.get("rate_per_hr", 0.0)
            labor_cost = a["estimated_time_hr"] * rate
            processes.append(
                MachineProcess(
                    rate_per_hr=rate,
                    labor_cost=labor_cost,
                    **{k: v for k, v in a.items()},
                )
            )
        return processes
