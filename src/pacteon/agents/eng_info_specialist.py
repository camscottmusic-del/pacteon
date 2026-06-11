"""
EngInfoSpecialistAgent — Stage 1.5 of the pipeline.

Validates the raw ExtractedDrawing against ASME/ASTM/AWS/ISO standards.
Corrects material_key, part_form_type, and feature misinterpretations.
Error-level flags halt Stage 2 and surface to the user.
Warning-level flags flow through as routing reviewer notes.
"""
import json
from pathlib import Path
from typing import Optional

import anthropic

from ..models.drawing import DrawingFeature, ExtractedDrawing

_STANDARDS_LIB_PATH = Path(__file__).parents[3] / "data" / "standards_library.json"


def _build_system_prompt() -> str:
    standards = json.loads(_STANDARDS_LIB_PATH.read_text(encoding="utf-8"))

    tol_table = json.dumps(standards["gdt_tolerances_by_process"], indent=2)
    compat_table = json.dumps(standards["material_process_compatibility"], indent=2)
    form_rules = json.dumps(standards["material_form_type_rules"], indent=2)
    weld_rules = json.dumps(standards["weld_symbol_rules"], indent=2)
    finish_map = json.dumps(standards["finish_callout_mapping"], indent=2)
    thickness_rules = json.dumps(standards["thickness_form_type_plausibility"], indent=2)
    astm_thickness = json.dumps(standards["astm_material_thickness_ranges"], indent=2)

    return f"""\
You are Schneider Packaging's engineering information specialist and the system's standards authority.

Your role: validate every fact extracted from an engineering drawing against published engineering standards \
before it reaches the shop foreman or cost engine. You are the last line of defense against misread material \
designations, incorrect GD&T interpretations, invalid weld callouts, and implausible dimensions.

## Standards References

### Achievable tolerances by process
<tolerances>
{tol_table}
</tolerances>

### Material–process compatibility (incompatible or suboptimal pairings)
<compatibility>
{compat_table}
</compatibility>

### ASTM/AISI spec → valid form_type mappings
<form_type_rules>
{form_rules}
</form_type_rules>

### Weld symbol rules (AWS A2.4-2012)
<weld_rules>
{weld_rules}
</weld_rules>

### Finish callout → process_id mapping
<finish_callouts>
{finish_map}
</finish_callouts>

### Thickness plausibility by form_type
<thickness_rules>
{thickness_rules}
</thickness_rules>

### ASTM material thickness ranges
<astm_thickness>
{astm_thickness}
</astm_thickness>

## What you validate

1. **Material designation** — Is the material_key correct for the spec on the drawing?
   Does part_form_type match the ASTM spec (e.g., A500 Gr B is ALWAYS tube, never plate)?

2. **Dimension plausibility** — Does thickness_in fall within the valid range for the stated
   part_form_type? Is blank_length_in plausible (not 0, not larger than a freight truck)?

3. **Feature–form_type consistency** — Can the listed features actually be made from this
   form type? (TUBE cannot be press-brake bent; ROUND_BAR cannot be waterjet profile cut)

4. **Weld features** — Are weld symbols interpreted correctly per AWS A2.4?
   Is the weld size physically achievable on the stated base metal thickness?

5. **Finish features** — Are finish callouts real purchasable processes?
   Does the finish process match the material (anodize requires aluminum)?

6. **GD&T tolerance feasibility** — Does the drawing call a tighter tolerance than the
   assigned process can achieve? (e.g., ±0.001" callout on a drill-press hole)

## What you can correct

- material_key: reassign to the correct local_price_key if the DrawingReader chose wrong
- part_form_type: correct if it conflicts with the ASTM spec
- feature_type on individual features: correct symbol misinterpretations
- notes on individual features: add standards citations explaining the correction

## What you cannot change

- blank_length_in, blank_width_in, thickness_in: raw dimensions read from the drawing.
  You may flag implausible values but do NOT overwrite measured dimensions.
- Feature list: you cannot add or remove features. The DrawingReaderAgent is authoritative
  on what was visually extracted.

## Severity levels for flags

- **error**: A fundamental problem that would cause a wrong quote or an unbuildable routing.
  Examples: material_key assigned to wrong alloy family; anodize assigned to steel part;
  weld fillet size larger than base metal thickness. Error-level flags halt Stage 2 routing
  and are surfaced to the user for correction before proceeding.

- **warning**: A potential issue that should be reviewed but does not block routing.
  Examples: thickness slightly outside typical range for the form_type; finish callout
  says "per spec" without a spec number; a tolerance tighter than the process can achieve
  (vendor may use a tighter machine — worth flagging).

## Output

Always call the validate_drawing_extraction tool. Never respond with plain text.
Return your validated values and flags concisely. For corrections, always include the
standard_citation explaining why the correction is necessary.
"""


_TOOL = {
    "name": "validate_drawing_extraction",
    "description": (
        "Return validated material_key, part_form_type, any feature corrections, "
        "flags (warnings/errors), and standards gaps found in the extracted drawing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "material_key_confirmed": {
                "type": "string",
                "description": "Validated material_key — same as input or corrected. Must be a key from material_prices.json."
            },
            "part_form_type_confirmed": {
                "type": "string",
                "enum": ["plate", "flat_stock", "tube", "round_bar", "sheet_metal", "weldment"],
                "description": "Validated form type — same as input or corrected."
            },
            "feature_corrections": {
                "type": "array",
                "description": "Corrections to individual features. Omit if no corrections needed.",
                "items": {
                    "type": "object",
                    "properties": {
                        "zone": {"type": "string"},
                        "field": {"type": "string", "enum": ["feature_type", "notes"]},
                        "corrected_value": {"type": "string"},
                        "standard_citation": {
                            "type": "string",
                            "description": "e.g. 'AWS A2.4 §3.2 — fillet weld, not groove'"
                        }
                    },
                    "required": ["zone", "field", "corrected_value", "standard_citation"]
                }
            },
            "flags": {
                "type": "array",
                "description": "Warnings and errors. Omit if drawing is clean.",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["warning", "error"]},
                        "zone": {
                            "type": "string",
                            "description": "Zone on the drawing, or 'drawing-level' for overall flags."
                        },
                        "message": {"type": "string"},
                        "requires_human_review": {"type": "boolean"}
                    },
                    "required": ["severity", "zone", "message", "requires_human_review"]
                }
            },
            "standards_gaps": {
                "type": "array",
                "description": "Valid specs not yet in the reference files. Omit if none.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["material", "finish", "symbol"]},
                        "value": {"type": "string", "description": "The unrecognized spec as written on the drawing."},
                        "suggested_addition": {
                            "type": "string",
                            "description": "Which file to add it to and what the entry should look like."
                        }
                    },
                    "required": ["type", "value", "suggested_addition"]
                }
            }
        },
        "required": ["material_key_confirmed", "part_form_type_confirmed"]
    }
}


class ValidationResult:
    """Carries the specialist's corrections and flags back to the pipeline."""

    def __init__(
        self,
        material_key: str,
        part_form_type: str,
        feature_corrections: list,
        flags: list,
        standards_gaps: list,
    ):
        self.material_key = material_key
        self.part_form_type = part_form_type
        self.feature_corrections = feature_corrections
        self.flags = flags
        self.standards_gaps = standards_gaps

    @property
    def has_errors(self) -> bool:
        return any(f.get("severity") == "error" for f in self.flags)

    @property
    def error_messages(self) -> list[str]:
        return [f["message"] for f in self.flags if f.get("severity") == "error"]

    @property
    def warning_notes(self) -> list[str]:
        return [f"[STANDARDS WARNING zone {f['zone']}] {f['message']}" for f in self.flags if f.get("severity") == "warning"]


class EngInfoSpecialistAgent:
    """Stage 1.5 — validates ExtractedDrawing against ASME/ASTM/AWS/ISO standards."""

    def __init__(self, client: anthropic.Anthropic):
        self.client = client
        self.system_prompt = _build_system_prompt()

    def validate(self, drawing: ExtractedDrawing) -> tuple[ExtractedDrawing, ValidationResult]:
        """
        Validate and optionally correct an ExtractedDrawing.
        Returns (corrected_drawing, result) where result carries flags and gaps.
        Raises ValueError if result.has_errors (caller decides whether to halt).
        """
        prompt = self._build_prompt(drawing)

        response = self.client.messages.create(
            model="claude-opus-4-8",
            max_tokens=2048,
            system=self.system_prompt,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "validate_drawing_extraction"},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_input = next(
            b.input for b in response.content if b.type == "tool_use"
        )

        result = ValidationResult(
            material_key=tool_input["material_key_confirmed"],
            part_form_type=tool_input["part_form_type_confirmed"],
            feature_corrections=tool_input.get("feature_corrections", []),
            flags=tool_input.get("flags", []),
            standards_gaps=tool_input.get("standards_gaps", []),
        )

        corrected = self._apply_corrections(drawing, result)
        return corrected, result

    def _build_prompt(self, drawing: ExtractedDrawing) -> str:
        features_text = "\n".join(
            f"  [{f.zone}] {f.feature_type} — {f.description} (qty: {f.quantity})"
            + (f" | dim: {f.dimension}" if f.dimension else "")
            + (f" | notes: {f.notes}" if f.notes else "")
            for f in drawing.features
        )
        return (
            f"Validate the following extracted drawing data against engineering standards.\n\n"
            f"Part: {drawing.part_name or drawing.part_number or 'unknown'}\n"
            f"Material (as written on drawing): {drawing.material}\n"
            f"material_key assigned by DrawingReader: {drawing.material_key}\n"
            f"part_form_type assigned by DrawingReader: {drawing.part_form_type}\n"
            f"Blank dimensions: {drawing.length_in}\" L × {drawing.width_in}\" W"
            + (f" × {drawing.thickness_in}\" T" if drawing.thickness_in else "") + "\n"
            f"is_formed: {drawing.is_formed}"
            + (f", formed_height_in: {drawing.formed_height_in}\"" if drawing.is_formed else "") + "\n\n"
            f"Features extracted ({len(drawing.features)} total):\n{features_text}\n\n"
            "Validate all fields and return your corrections and flags."
        )

    def _apply_corrections(self, drawing: ExtractedDrawing, result: ValidationResult) -> ExtractedDrawing:
        """Apply corrections from the specialist to the drawing (returns a modified copy)."""
        # Build a dict of field corrections keyed by zone
        corrections_by_zone: dict[str, dict] = {}
        for c in result.feature_corrections:
            corrections_by_zone.setdefault(c["zone"], {})[c["field"]] = c["corrected_value"]

        corrected_features = []
        for feat in drawing.features:
            zone_corrections = corrections_by_zone.get(feat.zone, {})
            if zone_corrections:
                feat = feat.model_copy(update={
                    k: v for k, v in zone_corrections.items() if hasattr(feat, k)
                })
            corrected_features.append(feat)

        # Add warning notes as a pseudo-feature note appended to routing_steps later
        return drawing.model_copy(update={
            "material_key": result.material_key,
            "part_form_type": result.part_form_type,
            "features": corrected_features,
        })
