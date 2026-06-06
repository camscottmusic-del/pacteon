"""
MachineWorkerAgent — Stage 3 (optional refinement).

Each virtual worker simulates a specific machine operation in detail,
refining the foreman's time estimate based on feature geometry.
This is a lightweight wrapper — most logic lives in the foreman.
Use this agent when you need a more precise cycle-time estimate for
complex features (multi-pass operations, long weld beads, etc.).
"""
import anthropic

from ..models.machine import MachineProcess

_SYSTEM_PROMPT = """\
You are a skilled CNC operator / machinist at Schneider Packaging.
You have been handed a single work order by the shop foreman.

Given the feature description, machine, tool, and material, calculate a refined
cycle-time estimate in hours. Be specific — account for tool passes, feed rates,
and realistic operator loading/unloading time.

Call the refine_estimate tool with your answer.
"""

_REFINE_TOOL: anthropic.types.ToolParam = {
    "name": "refine_estimate",
    "description": "Return a refined cycle-time estimate for one machine operation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "refined_time_hr": {"type": "number"},
            "reasoning": {"type": "string"},
        },
        "required": ["refined_time_hr", "reasoning"],
    },
}


class MachineWorkerAgent:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    def refine(self, process: MachineProcess, material: str) -> MachineProcess:
        """Ask a virtual machine worker to refine the foreman's time estimate."""
        prompt = (
            f"Work order:\n"
            f"  Machine: {process.machine_type} (tool: {process.tool_used})\n"
            f"  Feature: {process.feature_description} (zone {process.feature_zone})\n"
            f"  Material: {material}\n"
            f"  Foreman estimate: {process.estimated_time_hr:.3f} hr\n\n"
            f"Refine the cycle-time estimate."
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-6",   # Faster/cheaper for per-feature refinement
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            tools=[_REFINE_TOOL],
            tool_choice={"type": "tool", "name": "refine_estimate"},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_block = next(b for b in response.content if b.type == "tool_use")
        refined_time = tool_block.input["refined_time_hr"]

        return process.model_copy(
            update={
                "estimated_time_hr": refined_time,
                "labor_cost": refined_time * process.rate_per_hr,
                "notes": (process.notes or "") + f" [refined: {tool_block.input['reasoning']}]",
            }
        )
