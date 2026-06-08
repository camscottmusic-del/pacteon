"""
DrawingReaderAgent — Stage 1 of the pipeline.

Sends the engineering drawing PDF (as vision images + extracted text) to Claude
and receives a structured ExtractedDrawing back via tool_use.
"""
import json
from pathlib import Path

import anthropic

from ..models.drawing import DrawingFeature, ExtractedDrawing
from ..tools.pdf_extractor import extract_pdf_text, extract_pdf_images

_STANDARDS_PATH = Path(__file__).parents[3] / "data" / "drawing_standards.json"


def _build_system_prompt() -> str:
    standards = json.loads(_STANDARDS_PATH.read_text())
    standards_context = json.dumps(standards, indent=2)
    return f"""\
You are an expert manufacturing engineer who reads 2D engineering drawings (sheet metal parts, machined parts, weldments, etc.).

You have the following ANSI/ASME/ISO drawing standards reference. Use it to correctly interpret every symbol, notation, material code, and feature callout you encounter:

<drawing_standards>
{standards_context}
</drawing_standards>

## Dimension rules — critical
- Dimensions in PARENTHESES e.g. (4.44) = REFERENCE — see drawing_standards.dimension_notation. NEVER use for blank_length_in or blank_width_in.
- Read ALL views: top, front, right side, section, auxiliary, detail. Cross-reference them.

## Blank dimensions (cutlist / material cost)
- blank_length_in and blank_width_in = FLAT BLANK size before any forming.
- Flat part: overall length × width.
- Formed/bent part: blank_length_in = SUM of all flat profile segments (e.g. 3.50 + 1.00 = 4.50"). Do NOT use the overall formed dimension.
- is_formed = true if part has any bends, flanges, or formed features.
- formed_height_in = tallest leg or flange height from side/profile view.

## Part form type — use material_form_codes from drawing_standards to identify
- **plate** — PL, PLATE: laser/waterjet cut, priced per sq in
- **flat_stock** — FS, FLAT, BAR (flat): milled, priced per linear ft
- **tube** — HSS, DOM, ERW, TUBE, PIPE: tube laser, priced per linear ft
- **round_bar** — RD, ROD: lathe/mill, priced per linear ft
- **sheet_metal** — SHT, SHEET: all ops outsourced (laser→press brake→tap→weld→finish)
- **weldment** — multi-component assembly

## Material
Extract full designation from title block or BOM. Use material_spec_prefixes from drawing_standards to match material_key.

## Features
Extract every feature using hole_callout_terms, gdt_symbols, weld_symbols, and finish_callout_terms from drawing_standards. Include finish operations only if explicitly called out.

Always call the extract_drawing tool — do not respond with plain text.
"""


_SYSTEM_PROMPT = _build_system_prompt()

_EXTRACT_TOOL: anthropic.types.ToolParam = {
    "name": "extract_drawing",
    "description": "Record all material and feature data extracted from the engineering drawing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "part_number": {"type": "string"},
            "part_name": {"type": "string"},
            "revision": {"type": "string"},
            "material": {"type": "string", "description": "Full material name as written on the drawing BOM"},
            "material_key": {
                "type": "string",
                "description": "Best-match key from: A36_STEEL, 304_STAINLESS, 6061_ALUMINUM",
            },
            "part_form_type": {
                "type": "string",
                "enum": ["plate", "flat_stock", "tube", "round_bar", "sheet_metal", "weldment"],
                "description": "Form type of the raw stock. Drives routing and pricing unit.",
            },
            "blank_length_in": {"type": "number", "description": "Flat blank length (sum of profile segments for formed parts). Never use parenthetical reference dimensions."},
            "blank_width_in": {"type": "number", "description": "Flat blank width. Never use parenthetical reference dimensions."},
            "thickness_in": {"type": "number", "description": "Material thickness / gauge in inches"},
            "is_formed": {"type": "boolean", "description": "True if the part has any bends, flanges, or forming operations"},
            "formed_height_in": {"type": "number", "description": "Height of the tallest formed leg or flange, from the side/profile view"},
            "features": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "zone": {"type": "string"},
                        "feature_type": {"type": "string"},
                        "description": {"type": "string"},
                        "quantity": {"type": "integer"},
                        "dimension": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["zone", "feature_type", "description", "quantity"],
                },
            },
        },
        "required": ["material", "blank_length_in", "blank_width_in", "features"],
    },
}


class DrawingReaderAgent:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    def read(self, pdf_path: str | Path) -> ExtractedDrawing:
        """Parse a drawing PDF and return a structured ExtractedDrawing."""
        pdf_path = Path(pdf_path)
        text_pages = extract_pdf_text(pdf_path)
        image_pages = extract_pdf_images(pdf_path)

        # Build a multi-modal message: images first, then extracted text as context
        content: list = []
        for page_img in image_pages:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": page_img["media_type"],
                    "data": page_img["data"],
                },
            })
        text_dump = "\n\n".join(
            f"=== {k} ===\n{v}" for k, v in text_pages.items() if v.strip()
        )
        if text_dump:
            content.append({"type": "text", "text": f"Extracted text from drawing:\n\n{text_dump}"})

        response = self.client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            tools=[_EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "extract_drawing"},
            messages=[{"role": "user", "content": content}],
        )

        tool_block = next(b for b in response.content if b.type == "tool_use")
        data = dict(tool_block.input)
        # Map blank dimensions to model fields
        data["length_in"] = data.pop("blank_length_in")
        data["width_in"] = data.pop("blank_width_in")
        data["features"] = [DrawingFeature(**f) for f in data.get("features", [])]
        return ExtractedDrawing(**data)
