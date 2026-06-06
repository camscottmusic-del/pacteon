from pydantic import BaseModel
from typing import Optional


class MachineProcess(BaseModel):
    """One machine operation assigned by the shop foreman to produce a feature."""
    feature_zone: str           # Which drawing zone this addresses
    feature_description: str
    machine_id: str             # Key from machines.json, e.g. "CNC_MILL_1"
    machine_type: str           # e.g. "CNC_Mill"
    tool_used: str              # e.g. "counterbore"
    estimated_time_hr: float
    setup_time_hr: float
    rate_per_hr: float
    labor_cost: float           # estimated_time_hr * rate_per_hr
    notes: Optional[str] = None

    @property
    def total_cost(self) -> float:
        return (self.estimated_time_hr + self.setup_time_hr) * self.rate_per_hr
