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
You are an expert manufacturing engineer who reads 2D engineering assembly drawings.

Your job is to extract two things from the drawing provided:
1. The raw material specified in the Bill of Materials (BOM) — note the material name and any standard designation (e.g. "A36 Mild Steel", "6061 Aluminum T6").
2. Every manufacturing feature visible in the drawing zones — holes, welds, taps, threads, bends, counterbores, countersinks, slots, pockets, etc.

For each feature record:
- The drawing zone label (e.g. "A8", "B3", or "Title Block")
- Feature type (hole, weld, tap, bend, slot, counterbore, countersink, pocket, thread, etc.)
- Quantity and dimension where shown
- A plain-English description

Also extract the part's overall dimensions (length × width, and thickness if shown).

Always call the extract_drawing tool with your findings — do not respond with plain text.
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
            "length_in": {"type": "number"},
            "width_in": {"type": "number"},
            "thickness_in": {"type": "number"},
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
        "required": ["material", "length_in", "width_in", "features"],
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
        data = tool_block.input
        data["features"] = [DrawingFeature(**f) for f in data.get("features", [])]
        return ExtractedDrawing(**data)
