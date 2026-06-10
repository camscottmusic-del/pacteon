"""
Deterministic process time and cost calculator.

Given a process ID and feature parameters, returns time_hr and setup_hr
using formulas from process_library.json. No AI involved — pure math.
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
    """Build the feed-rate lookup key, e.g. 'A36_STEEL_0250'."""
    if not thickness_in:
        return "DEFAULT"
    t_str = f"{thickness_in:.3f}".replace(".", "")
    return f"{material_key}_{t_str}"


def _area_tier(blank_area_sq_in: float, tiers: dict) -> float:
    """Return time_hr for the appropriate area tier."""
    for tier in tiers.values():
        if tier["max_sq_in"] is None or blank_area_sq_in <= tier["max_sq_in"]:
            return tier["time_hr"]
    return list(tiers.values())[-1]["time_hr"]


def calc_setup_time(
    process_id: str,
    quantity: int = 1,
    pierce_count: int = 0,
) -> float:
    """
    Return setup time in hours for one process, scaled by features/size.

    Setup is dynamic — a 2-hole bracket has a shorter laser setup than a
    40-hole plate. Formulas live in process_library.json under 'setup_formula'.

    Args:
        process_id:   Key matching process_library.json
        quantity:     Number of operations (bends, welds, features) — drives
                      setup for count-based processes
        pierce_count: Number of pierce points — drives setup for geometry
                      processes (LASER_CUT, WATERJET, TUBE_LASER)
    """
    library = _load_library()
    if process_id not in library:
        raise ValueError(f"Unknown process '{process_id}'.")

    proc = library[process_id]
    sf = proc.get("setup_formula")
    if not sf:
        return 0.0

    base = sf["base_hr"]

    if "per_pierce_hr" in sf:
        # Geometry processes: setup scales with pierce count
        dynamic = base + pierce_count * sf["per_pierce_hr"]
    elif "per_bend_hr" in sf:
        # Press brake: each additional bend may need angle adjust / tool change
        dynamic = base + max(0, quantity - 1) * sf["per_bend_hr"]
    elif "per_weld_hr" in sf:
        # Weld: each joint adds fixture time
        dynamic = base + quantity * sf["per_weld_hr"]
    elif "per_feature_hr" in sf:
        # Mill / lathe / finish: features add program/tool complexity
        dynamic = base + quantity * sf["per_feature_hr"]
    else:
        dynamic = base

    return min(dynamic, sf["max_hr"])


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
    Return run time in hours (excluding setup) for one process operation.

    Args:
        process_id:       Key matching process_library.json
        quantity:         Number of operations (bends, taps, welds, etc.)
        material_key:     e.g. 'A36_STEEL', '304_STAINLESS'
        thickness_in:     Material thickness (for geometry feed-rate lookup)
        blank_area_sq_in: Blank area (for area_tier finish processes)
        cut_length_in:    Total cut perimeter (for geometry/laser formulas)
        pierce_count:     Number of pierce points (for laser formulas)
    """
    library = _load_library()
    if process_id not in library:
        raise ValueError(f"Unknown process '{process_id}'. Add it to data/process_library.json.")

    proc = library[process_id]
    formula = proc["formula_type"]

    if formula == "count":
        return proc["time_per_unit_hr"] * quantity

    if formula == "bend_geometry":
        # Each bend: base time + length-scaling for positioning/handling.
        # cut_length_in = bend chord length in inches (passed by foreman).
        bend_length = cut_length_in if cut_length_in else 12.0  # 12" default if not provided
        return quantity * (proc["time_per_bend_hr"] + bend_length * proc["time_per_in_hr"])

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

    if formula == "area_linear":
        # Continuously scales with blank area — larger part = more surface = more paint/coat time.
        # blank_area is used as a proxy for total painted surface area.
        base = proc["base_time_hr"]
        run = base + blank_area_sq_in * proc["time_per_sq_in_hr"]
        return min(run, proc.get("max_time_hr", run))

    raise ValueError(f"Unknown formula_type '{formula}' for process '{process_id}'.")


def calc_process_cost(
    process_id: str,
    run_time_hr: float,
    setup_time_hr: float,
) -> tuple[float, float]:
    """
    Return (setup_cost, run_cost) given pre-computed times.
    Total cost = setup_cost + run_cost.
    """
    vendors = _load_vendors()
    if process_id not in vendors:
        raise ValueError(f"Unknown process '{process_id}'. Add it to data/vendor_processes.json.")

    rate = vendors[process_id]["rate_per_hr"]
    return setup_time_hr * rate, run_time_hr * rate
