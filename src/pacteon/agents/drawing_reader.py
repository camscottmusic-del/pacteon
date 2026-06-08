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

_SYSTEM_PROMPT = """\
You are an expert manufacturing engineer who reads 2D engineering drawings (sheet metal parts, machined parts, weldments, etc.).

## Dimension rules — critical
- Dimensions shown in PARENTHESES, e.g. (4.44), are REFERENCE dimensions. NEVER use them for blank_length_in or blank_width_in.
- Use only the nominal (non-parenthetical) dimensions for all calculations.
- Read ALL views present: top, front, right side, section, auxiliary, detail. Cross-reference them.

## Blank dimensions (for cutlist / material cost)
- blank_length_in and blank_width_in are the FLAT BLANK dimensions — the size of raw stock that gets cut before any forming.
- For a FLAT part: blank_length_in = overall length, blank_width_in = overall width.
- For a FORMED/BENT part (L-bracket, U-channel, Z-bracket, etc.): blank_length_in = SUM of all flat segments in the profile view (e.g. 3.50 + 1.00 = 4.50"). Do NOT use the overall formed length.
- is_formed = true if the part has any bends, flanges, or formed features.
- formed_height_in = the tallest leg or flange height from the profile/side view (e.g. 1.50").

## Part form type — determines routing and pricing unit
Identify part_form_type from the material designation and geometry. Choose exactly one:

- **plate** — flat sheet or plate material (e.g. "A36 PL", "304SHT", "6061 PLATE"). Cut by waterjet or laser. Priced per sq in.
- **flat_stock** — flat bar or structural stock (e.g. "1x4 flat bar", "A36 FS"). Machined/milled. Priced per linear ft.
- **tube** — square, rectangular, or round tube (e.g. "HSS 2x2x.125", "DOM TUBE"). Cut on tube laser. Priced per linear ft.
- **round_bar** — solid round bar or rod (e.g. "1.5" DIA 1018", "6061 ROD"). Turned on lathe or milled. Priced per linear ft.
- **sheet_metal** — thin formed sheet metal. ALL operations outsourced to vendors (laser, press brake, tap, weld, paint, powder coat, anodize). Priced via vendor rates.
- **weldment** — assembly of multiple components; each component priced by its own form_type.

## Material
Extract the full material designation from the title block or BOM (e.g. "304SHT", "A36", "6061-T6"). Match to the closest material_key.

## Features
Extract every manufacturing feature: holes, slots, taps, threads, welds, bends, counterbores, countersinks, pockets, notches, finish operations (paint, powder coat, anodize).

Always call the extract_drawing tool — do not respond with plain text.
"""

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
