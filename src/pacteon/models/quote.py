from pydantic import BaseModel, Field
from typing import Optional
from datetime import date


class LineItem(BaseModel):
    description: str
    quantity: float
    unit: str
    unit_price: float
    total: float


class Quote(BaseModel):
    """Complete should-cost estimate for one part."""
    quote_date: date = Field(default_factory=date.today)
    part_number: Optional[str] = None
    part_name: Optional[str] = None
    revision: Optional[str] = None

    # Cost breakdown
    material_cost: float = 0.0
    labor_cost: float = 0.0
    machine_cost: float = 0.0
    overhead_pct: float = 0.15         # 15% default overhead — adjust per Schneider actuals
    margin_pct: float = 0.10           # 10% margin

    line_items: list[LineItem] = Field(default_factory=list)

    # Machine routing for ERP upload
    routing_steps: list[str] = Field(default_factory=list)

    @property
    def subtotal(self) -> float:
        return self.material_cost + self.labor_cost + self.machine_cost

    @property
    def overhead(self) -> float:
        return self.subtotal * self.overhead_pct

    @property
    def margin(self) -> float:
        return (self.subtotal + self.overhead) * self.margin_pct

    @property
    def total_price(self) -> float:
        return self.subtotal + self.overhead + self.margin

    def summary(self) -> dict:
        return {
            "part": f"{self.part_number} {self.part_name} rev{self.revision}",
            "material_cost": round(self.material_cost, 2),
            "labor_cost": round(self.labor_cost, 2),
            "machine_cost": round(self.machine_cost, 2),
            "overhead": round(self.overhead, 2),
            "margin": round(self.margin, 2),
            "total_price": round(self.total_price, 2),
            "routing_steps": self.routing_steps,
        }
