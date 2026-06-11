"""
EngDocSpecialistAgent — Stage 2.5 process standards gate.

Validates process assignments against engineering standards (tolerance achievability,
material-process compatibility, ASME/AWS/OSHA constraints). Runs alongside the domain
specialists in the SpecialistDispatcher. Uses the same review_process_assignments tool
but reviews ALL processes, not just a single domain.
"""
import json
from pathlib import Path

import anthropic

from ._specialist_base import _REVIEW_TOOL
from ..models.drawing import ExtractedDrawing
from ..models.machine import MachineProcess

_STANDARDS_LIB_PATH = Path(__file__).parents[3] / "data" / "standards_library.json"


def _build_system_prompt() -> str:
    standards = json.loads(_STANDARDS_LIB_PATH.read_text(encoding="utf-8"))
    tol_table = json.dumps(standards["gdt_tolerances_by_process"], indent=2)
    compat_table = json.dumps(standards["material_process_compatibility"], indent=2)

    return f"""\
You are Schneider Packaging's engineering documentation specialist. Your role is to validate \
process assignments against published engineering standards and flag any combinations that are \
non-compliant or technically problematic.

You review ALL processes assigned to a part, regardless of domain. You are NOT a domain \
process expert — you are the standards compliance gate. You check:

1. **Tolerance achievability** — Does the drawing call out a tighter tolerance than the assigned
   process can reliably achieve?
2. **Material-process compatibility** — Is the assigned process compliant for the part's material?
3. **Process sequencing standards** — Are there ASME, AWS, or industry sequencing requirements
   that the current assignment violates?

## Achievable tolerances by process (ASME Y14.5 / Machinery's Handbook)
<tolerances>
{tol_table}
</tolerances>

## Material-process compatibility constraints
<compatibility>
{compat_table}
</compatibility>

## What you flag in efficiency_note

For each assignment, either confirm it passes ("standards compliant") or flag:
- Tolerance issue: "TOLERANCE FLAG: drawing calls ±X — this process achieves ±Y minimum; \
  upgrade to [process] for this tolerance"
- Compatibility issue: "STANDARDS FLAG: [material] + [process] — [reason from compatibility table]"
- Sequencing issue: "SEQUENCE FLAG: [description of the issue]"

You do NOT change cut_length_in, pierce_count, or quantity values — those are the domain
specialists' responsibility. You only add standards compliance notes to efficiency_note.
Return the same values as input for all numeric parameters.

If an assignment passes all standards checks, return efficiency_note = "standards compliant".

Always call review_process_assignments. Return one entry per input process.
"""


class EngDocSpecialistAgent:
    """Stage 2.5 — validates all process assignments against engineering standards."""

    def __init__(self, client: anthropic.Anthropic):
        self.client = client
        self.system_prompt = _build_system_prompt()

    def review(self, drawing: ExtractedDrawing, all_processes: list[MachineProcess]) -> list[MachineProcess]:
        """
        Review all processes for standards compliance.
        Appends standards notes to process.notes but does not change numeric parameters.
        """
        if not all_processes:
            return all_processes

        prompt = self._build_prompt(drawing, all_processes)

        response = self.client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            system=self.system_prompt,
            tools=[_REVIEW_TOOL],
            tool_choice={"type": "tool", "name": "review_process_assignments"},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_input = next(b.input for b in response.content if b.type == "tool_use")
        reviewed = {a["feature_zone"] + a["process_id"]: a for a in tool_input["assignments"]}

        result = []
        for proc in all_processes:
            key = proc.feature_zone + (proc.process_id or proc.machine_type)
            update = reviewed.get(key)
            if update:
                note = update.get("efficiency_note", "")
                if note and note.lower() != "standards compliant":
                    existing = proc.notes or ""
                    separator = " | " if existing else ""
                    proc = proc.model_copy(update={
                        "notes": f"{existing}{separator}[eng-doc] {note}"
                    })
            result.append(proc)
        return result

    def _build_prompt(self, drawing: ExtractedDrawing, processes: list[MachineProcess]) -> str:
        lines = [
            f"Part: {drawing.part_name or drawing.part_number or 'unknown'}",
            f"Material: {drawing.material} ({drawing.material_key}) | form_type: {drawing.part_form_type}",
            f"Blank: {drawing.length_in}\" L × {drawing.width_in}\" W"
            + (f" × {drawing.thickness_in}\" T" if drawing.thickness_in else ""),
            "",
            "All process assignments — validate standards compliance for each:",
        ]
        for p in processes:
            lines.append(
                f"  zone={p.feature_zone} process={p.process_id or p.machine_type} | "
                f"{p.feature_description}"
            )
        lines.append(
            "\nFor each assignment: add standards compliance note to efficiency_note. "
            "Do not change numeric parameters — return the same cut_length_in, pierce_count, and quantity as input."
        )
        return "\n".join(lines)
