"""Tests for the deterministic cost calculator — no AI calls needed."""
import pytest
from pacteon.tools.cost_calculator import calc_material_cost, calc_machine_cost
from pacteon.tools.process_calculator import calc_process_time, calc_process_cost


# --- material cost ---

def test_material_cost_basic():
    unit, total = calc_material_cost("A36_STEEL", length_in=12, width_in=12, quantity=1)
    assert unit == pytest.approx(144 * 0.045)
    assert total == unit


def test_material_cost_quantity():
    unit, total = calc_material_cost("A36_STEEL", length_in=12, width_in=12, quantity=5)
    assert total == pytest.approx(unit * 5)


def test_material_cost_new_keys():
    """All new material keys should resolve without error."""
    for key in ["A572_GR50", "A500_GRB", "A1008_CRCS", "1018_STEEL",
                "304L_STAINLESS", "316_STAINLESS", "316L_STAINLESS",
                "5052_ALUMINUM", "6063_ALUMINUM"]:
        unit, total = calc_material_cost(key, length_in=10, width_in=10, quantity=1)
        assert unit > 0


def test_unknown_material_raises():
    with pytest.raises(ValueError, match="Unknown material key"):
        calc_material_cost("UNOBTAINIUM", 10, 10)


# --- process time ---

def test_process_time_count_drill():
    t = calc_process_time("DRILL", quantity=4)
    assert t == pytest.approx(4 * 0.017)


def test_process_time_count_cnc():
    t = calc_process_time("CNC_MILL", quantity=3)
    assert t == pytest.approx(3 * 0.033)


def test_process_time_linear_mig():
    t = calc_process_time("WELD_MIG", cut_length_in=12.0)
    assert t == pytest.approx(12.0 * 0.008)


def test_process_time_linear_tig():
    t = calc_process_time("WELD_TIG", cut_length_in=6.0)
    assert t == pytest.approx(6.0 * 0.020)


def test_process_time_geometry_laser():
    t = calc_process_time(
        "LASER_CUT",
        material_key="A36_STEEL",
        thickness_in=0.125,
        cut_length_in=60.0,
        pierce_count=4,
    )
    # feed rate for A36_STEEL_0125 = 120 in/min
    cut_hr = (60.0 / 120) / 60.0
    pierce_hr = 4 * 0.0008
    assert t == pytest.approx(cut_hr + pierce_hr)


def test_unknown_process_raises():
    with pytest.raises(ValueError, match="Unknown process"):
        calc_process_time("FAKE_PROCESS")


# --- process cost ---

def test_process_cost_cnc():
    setup, run = calc_process_cost("CNC_MILL", time_hr=1.0)
    assert setup == pytest.approx(0.5 * 85.0)
    assert run == pytest.approx(1.0 * 85.0)


def test_process_cost_no_setup():
    setup, run = calc_process_cost("CNC_MILL", time_hr=1.0, include_setup=False)
    assert setup == 0.0
    assert run == pytest.approx(1.0 * 85.0)
