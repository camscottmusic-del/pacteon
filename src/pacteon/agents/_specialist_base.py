"""
Shared base class and tool definition for all Stage 2.5 specialist agents.
Each specialist reviews process assignments from the Foreman and adjusts
cut_length_in, pierce_count, and quantity for its domain.
"""
from typing import Any

import anthropic

from ..models.drawing import ExtractedDrawing
from ..models.machine import MachineProcess

_REVIEW_TOOL: dict[str, Any] = {
    "name": "review_process_assignments",
    "description": (
        "Return reviewed process assignments with corrected parameters. "
        "Must return exactly one entry per input assignment. "
        "Do not add or remove entries. If values are correct, return them unchanged."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "feature_zone":    {"type": "string"},
                        "process_id":      {"type": "string", "description": "Must match the input process_id — do not change."},
                        "cut_length_in":   {"type": "number", "description": "Corrected or unchanged cut perimeter / weld bead length in inches."},
                        "pierce_count":    {"type": "integer", "description": "Corrected or unchanged pierce count."},
                        "quantity":        {"type": "integer", "description": "Corrected or unchanged operation count."},
                        "efficiency_note": {"type": "string", "description": "Brief reasoning for any change, or 'parameters confirmed' if unchanged."}
                    },
                    "required": ["feature_zone", "process_id", "cut_length_in", "pierce_count", "quantity", "efficiency_note"]
                }
            }
        },
        "required": ["assignments"]
    }
}


class BaseSpecialistAgent:
    """
    Base for all Stage 2.5 process specialists.
    Subclasses set `domain` (str) and `processes` (set[str]) and pass their system prompt.
    """

    domain: str = ""
    processes: set[str] = set()

    def __init__(self, client: anthropic.Anthropic, system_prompt: str):
        self.client = client
        self.system_prompt = system_prompt

    def review(self, drawing: ExtractedDrawing, domain_processes: list[MachineProcess]) -> list[MachineProcess]:
        """
        Review the subset of processes belonging to this specialist's domain.
        Returns the same list with updated parameters and specialist_reviewed=True.
        """
        if not domain_processes:
            return domain_processes

        prompt = self._build_prompt(drawing, domain_processes)

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=self.system_prompt,
            tools=[_REVIEW_TOOL],
            tool_choice={"type": "tool", "name": "review_process_assignments"},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_input = next(
            b.input for b in response.content if b.type == "tool_use"
        )

        reviewed = {a["feature_zone"] + a["process_id"]: a for a in tool_input["assignments"]}
        result = []
        for proc in domain_processes:
            key = proc.feature_zone + (proc.process_id or proc.machine_type)
            update = reviewed.get(key)
            if update:
                note_parts = []
                if proc.notes:
                    note_parts.append(proc.notes)
                if update["efficiency_note"] and update["efficiency_note"].lower() != "parameters confirmed":
                    note_parts.append(f"[{self.domain} specialist] {update['efficiency_note']}")
                proc = proc.model_copy(update={
                    "cut_length_in":       update.get("cut_length_in", proc.cut_length_in),
                    "pierce_count":        update.get("pierce_count",   proc.pierce_count),
                    "quantity":            update.get("quantity",        proc.quantity),
                    "specialist_reviewed": True,
                    "specialist_domain":   self.domain,
                    "notes":               " | ".join(note_parts) if note_parts else proc.notes,
                })
            result.append(proc)
        return result

    def _build_prompt(self, drawing: ExtractedDrawing, processes: list[MachineProcess]) -> str:
        lines = [
            f"Part: {drawing.part_name or drawing.part_number or 'unknown'}",
            f"Material: {drawing.material} | form_type: {drawing.part_form_type}",
            f"Blank: {drawing.length_in}\" L × {drawing.width_in}\" W"
            + (f" × {drawing.thickness_in}\" T" if drawing.thickness_in else ""),
            "",
            "Process assignments to review:",
        ]
        for p in processes:
            lines.append(
                f"  zone={p.feature_zone} process={p.process_id or p.machine_type} | "
                f"{p.feature_description} | "
                f"cut_length_in={p.cut_length_in:.1f} "
                f"pierce_count={p.pierce_count} "
                f"quantity={p.quantity}"
            )
        lines.append("\nReview each assignment and return corrected parameters.")
        return "\n".join(lines)
