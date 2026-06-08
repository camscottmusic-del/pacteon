"""
Deterministic process time calculator.

Given a process ID and feature parameters, returns time_hr using
formulas from process_library.json. No AI involved — pure math.
"""
import json
from pathlib import Path

_LIBRARY_PATH = Path(__file__).parents[3] / "data" / "process_library.json"
_VENDORS_PATH = Path(__file__).parents[3] / "data" / "vendor_processes.json"


def _load_library() -> dict:
    return json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))


def _load_vendors() -> dict:
    return json.loads(_VENDORS_PATH.read_text(encoding="utf-8"))


def _material_thickness_key(material_key: str, thickness_in: float | None) -> str:
    """Build the feed-rate lookup key, e.g. '304_STAINLESS_0125'."""
    if not thickness_in:
        return "DEFAULT"
    # Convert thickness to 4-digit string: 0.125 → "0125"
    t_str = f"{thickness_in:.3f}".replace(".", "")
    return f"{material_key}_{t_str}"


def _area_tier(blank_area_sq_in: float, tiers: dict) -> float:
    """Return time_hr for the appropriate area tier."""
    for tier in tiers.values():
        if tier["max_sq_in"] is None or blank_area_sq_in <= tier["max_sq_in"]:
            return tier["time_hr"]
    return list(tiers.values())[-1]["time_hr"]


def calc_process_time(
    process_id: str,
    quantity: int = 1,
    material_key: str = "DEFAULT",
    thickness_in: float | None = None,
    blank_area_sq_in: float = 0.0,
    cut_length_in: float = 0.0,
    pierce_count: int = 0,
) -> float:
    """
    Return estimated run time in hours (excluding setup) for one process operation.

    Args:
        process_id:       Key matching process_library.json (e.g. "LASER_CUT")
        quantity:         Number of operations (bends, taps, welds, etc.)
        material_key:     Material key (e.g. "304_STAINLESS")
        thickness_in:     Material thickness in inches (for geometry formulas)
        blank_area_sq_in: Blank length × width (for area_tier formulas)
        cut_length_in:    Total cut perimeter length (for geometry/laser formulas)
        pierce_count:     Number of pierce points / holes (for laser formulas)
    """
    library = _load_library()
    if process_id not in library:
        raise ValueError(f"Unknown process '{process_id}'. Add it to data/process_library.json.")

    proc = library[process_id]
    formula = proc["formula_type"]

    if formula == "count":
        return proc["time_per_unit_hr"] * quantity

    if formula == "geometry":
        feed_key = _material_thickness_key(material_key, thickness_in)
        feed_rates = proc.get("feed_rates_in_per_min", {})
        feed_rate = feed_rates.get(feed_key) or feed_rates.get("DEFAULT", 80)
        cut_time_hr = (cut_length_in / feed_rate) / 60.0 if cut_length_in else 0.0
        pierce_time_hr = proc.get("pierce_time_hr", 0.0) * pierce_count
        return cut_time_hr + pierce_time_hr

    if formula == "linear":
        return proc["time_per_in_hr"] * cut_length_in

    if formula == "area_tier":
        return _area_tier(blank_area_sq_in, proc["tiers"])

    raise ValueError(f"Unknown formula_type '{formula}' for process '{process_id}'.")


def calc_process_cost(
    process_id: str,
    time_hr: float,
    include_setup: bool = True,
) -> tuple[float, float]:
    """
    Return (setup_cost, run_cost) for a process given run time in hours.
    Total cost = setup_cost + run_cost.
    """
    vendors = _load_vendors()
    if process_id not in vendors:
        raise ValueError(f"Unknown process '{process_id}'. Add it to data/vendor_processes.json.")

    proc = vendors[process_id]
    rate = proc["rate_per_hr"]
    setup_cost = proc["setup_hr"] * rate if include_setup else 0.0
    run_cost = time_hr * rate
    return setup_cost, run_cost
