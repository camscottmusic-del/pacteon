"""Deterministic cost calculations — no AI involved here."""
import json
from pathlib import Path

_PRICES_PATH = Path(__file__).parents[3] / "data" / "material_prices.json"
_VENDORS_PATH = Path(__file__).parents[3] / "data" / "vendor_processes.json"


def _load_prices() -> dict:
    return json.loads(_PRICES_PATH.read_text())["materials"]


def _load_vendors() -> dict:
    return json.loads(_VENDORS_PATH.read_text())


def calc_material_cost(
    material_key: str,
    length_in: float,
    width_in: float,
    quantity: int = 1,
) -> tuple[float, float]:
    """
    Returns (unit_cost, total_cost) for a raw material cut.
    Price is per square inch as stored in material_prices.json.
    """
    prices = _load_prices()
    if material_key not in prices:
        raise ValueError(f"Unknown material key '{material_key}'. Add it to data/material_prices.json.")
    price_per_sq_in = prices[material_key]["price_per_sq_in"]
    sq_in = length_in * width_in
    unit_cost = sq_in * price_per_sq_in
    return unit_cost, unit_cost * quantity


def calc_machine_cost(
    process_id: str,
    run_time_hr: float,
    setup_time_hr: float | None = None,
) -> float:
    """Returns total cost for one vendor process including setup."""
    vendors = _load_vendors()
    if process_id not in vendors:
        raise ValueError(f"Unknown process '{process_id}'. Add it to data/vendor_processes.json.")
    proc = vendors[process_id]
    effective_setup = setup_time_hr if setup_time_hr is not None else proc["setup_hr"]
    return (run_time_hr + effective_setup) * proc["rate_per_hr"]
