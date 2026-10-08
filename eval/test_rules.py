"""Unit tests for the Phase 4 rules engine. Run: pytest eval/test_rules.py -v"""
import sys

import pytest

sys.path.insert(0, ".")

from src.rules import (  # noqa: E402
    calculate_return_fresh,
    call_function,
    can_edit_dc,
    can_end_trip,
    check_photo_requirements,
    pending_pickup_blocks_trip,
    validate_delivery,
    validate_pickup,
)


def test_return_fresh_basic():  # q002: 500/450 -> 50
    assert calculate_return_fresh(500, 450) == 50


def test_return_fresh_one_unit():  # q003: 500/499 -> 1
    assert calculate_return_fresh(500, 499) == 1


def test_return_fresh_zero():  # q004/q010 exact delivery
    assert calculate_return_fresh(500, 500) == 0


def test_return_fresh_per_product():  # q049: 500/460 -> 40, 300/300 -> 0
    assert calculate_return_fresh(500, 460) == 40
    assert calculate_return_fresh(300, 300) == 0


def test_over_delivery_raises():  # q001/q009: cannot exceed
    with pytest.raises(ValueError):
        calculate_return_fresh(500, 520)
    with pytest.raises(ValueError):
        validate_delivery(500, 501)
    validate_delivery(500, 500)  # exact is legal


def test_photo_counts():  # q032/q033/q050
    assert check_photo_requirements(0, 5) == 5  # 5 damaged towels
    assert check_photo_requirements(4, 0) == 4  # 4 heavy-soil towels
    assert check_photo_requirements(1, 1) == 2  # one unit with both


def test_pickup_validation():  # q034
    validate_pickup(10, 4, 3)
    with pytest.raises(ValueError):
        validate_pickup(10, 11, 0)
    with pytest.raises(ValueError):
        validate_pickup(10, 0, 12)


def test_end_trip():  # q028 blocks, q029 pickup does not
    assert can_end_trip([]) is True
    assert can_end_trip(["Hotel 2"]) is False
    assert pending_pickup_blocks_trip() is False


def test_dc_edit_windows():  # q010/q015/q016
    assert can_edit_dc(False, False).startswith("allowed:")
    assert can_edit_dc(False, True).startswith("allowed_with_regeneration")
    assert can_edit_dc(True, True).startswith("blocked")


def test_call_function_dispatch():
    assert call_function("calculate_return_fresh", {"expected": 500, "delivered": 450}) == {
        "ok": True,
        "result": 50,
    }
    assert call_function("calculate_return_fresh", {"expected": 500, "delivered": 501})["ok"] is False
    with pytest.raises(ValueError):
        call_function("nope", {})
