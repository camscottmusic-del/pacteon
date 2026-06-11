from pydantic import BaseModel
from typing import Optional


class MachineProcess(BaseModel):
    """One machine operation assigned by the shop foreman to produce a feature."""
    feature_zone: str           # Which drawing zone this addresses
    feature_description: str
    process_id: Optional[str] = None   # Key from vendor_processes.json, e.g. "LASER_CUT"
    machine_id: str             # Key from machines.json, e.g. "CNC_MILL_1"
    machine_type: str           # e.g. "CNC_Mill"
    tool_used: str              # e.g. "counterbore"
    quantity: int = 1                    # operation count (bends, taps, features)
    cut_length_in: float = 0.0          # perimeter or weld bead length (geometry/linear processes)
    pierce_count: int = 0               # number of pierce points (geometry processes)
    estimated_time_hr: float
    setup_time_hr: float
    rate_per_hr: float
    labor_cost: float           # estimated_time_hr * rate_per_hr
    notes: Optional[str] = None
    specialist_reviewed: bool = False
    specialist_domain: Optional[str] = None  # "cutting", "forming", "machining", "welding", "finishing"

    @property
    def total_cost(self) -> float:
        return (self.estimated_time_hr + self.setup_time_hr) * self.rate_per_hr
