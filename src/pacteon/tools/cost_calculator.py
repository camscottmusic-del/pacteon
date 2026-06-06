"""Deterministic cost calculations — no AI involved here."""
import json
from pathlib import Path

_PRICES_PATH = Path(__file__).parents[4] / "data" / "material_prices.json"
_MACHINES_PATH = Path(__file__).parents[4] / "data" / "machines.json"


def _load_prices() -> dict:
    return json.loads(_PRICES_PATH.read_text())["materials"]


def _load_machines() -> dict:
    return json.loads(_MACHINES_PATH.read_text())["machines"]


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
    machine_id: str,
    run_time_hr: float,
    setup_time_hr: float | None = None,
) -> float:
    """Returns total cost for one machine operation including setup."""
    machines = _load_machines()
    if machine_id not in machines:
        raise ValueError(f"Unknown machine '{machine_id}'. Add it to data/machines.json.")
    machine = machines[machine_id]
    effective_setup = setup_time_hr if setup_time_hr is not None else machine["setup_time_hr"]
    return (run_time_hr + effective_setup) * machine["rate_per_hr"]
