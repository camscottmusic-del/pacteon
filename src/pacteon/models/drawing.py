from pydantic import BaseModel, Field
from typing import Optional


class DrawingFeature(BaseModel):
    """A single manufacturing feature identified in a drawing zone."""
    zone: str                          # e.g. "A8", "B3"
    feature_type: str                  # e.g. "hole", "weld", "bend", "tap"
    description: str                   # e.g. '4x 1/4" through-hole'
    quantity: int = 1
    dimension: Optional[str] = None    # e.g. '1/4"', '0.250"'
    process_type: Optional[str] = None # Assigned by foreman: "CNC_Mill", "Welding", etc.
    notes: Optional[str] = None


class ExtractedDrawing(BaseModel):
    """Everything parsed from a single engineering drawing PDF."""
    part_number: Optional[str] = None
    part_name: Optional[str] = None
    revision: Optional[str] = None

    # Material from BOM
    material: str                      # e.g. "A36 Mild Steel"
    material_key: Optional[str] = None # Matches key in material_prices.json

    # Part dimensions for material cost calculation
    length_in: float
    width_in: float
    thickness_in: Optional[float] = None

    # Manufacturing features identified in the drawing
    features: list[DrawingFeature] = Field(default_factory=list)

    # Raw text extracted from the PDF (for debugging / reprocessing)
    raw_bom_text: Optional[str] = None
    raw_notes_text: Optional[str] = None
