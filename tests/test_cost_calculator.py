"""Tests for the deterministic cost calculator — no AI calls needed."""
import pytest
from pacteon.tools.cost_calculator import calc_material_cost, calc_machine_cost


def test_material_cost_basic():
    unit, total = calc_material_cost("A36_STEEL", length_in=12, width_in=12, quantity=1)
    assert unit == pytest.approx(144 * 0.045)
    assert total == unit


def test_material_cost_quantity():
    unit, total = calc_material_cost("A36_STEEL", length_in=12, width_in=12, quantity=5)
    assert total == pytest.approx(unit * 5)


def test_unknown_material_raises():
    with pytest.raises(ValueError, match="Unknown material key"):
        calc_material_cost("UNOBTAINIUM", 10, 10)


def test_machine_cost_cnc():
    cost = calc_machine_cost("CNC_MILL_1", run_time_hr=1.0, setup_time_hr=0.5)
    assert cost == pytest.approx(1.5 * 85.0)


def test_unknown_machine_raises():
    with pytest.raises(ValueError, match="Unknown machine"):
        calc_machine_cost("FAKE_MACHINE", run_time_hr=1.0)
