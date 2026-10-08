"""Phase 4 deterministic rules engine — pure functions, no LLM math.

Every quantitative or state-transition claim in an answer must come from
here (called directly or via LLM function-calling in src/generate.py).
See SPEC §2 for the source rules.
"""
from __future__ import annotations


def calculate_return_fresh(expected: int, delivered: int) -> int:
    """Return Fresh = Expected - Delivered. Raises on over-delivery."""
    if delivered > expected:
        raise ValueError(f"Delivered ({delivered}) cannot exceed Expected ({expected})")
    if expected < 0 or delivered < 0:
        raise ValueError("Quantities cannot be negative")
    return expected - delivered


def validate_delivery(expected: int, delivered: int) -> None:
    """Raises if Delivered > Expected (app blocks over-delivery)."""
    if delivered > expected:
        raise ValueError(f"Delivered ({delivered}) cannot exceed Expected ({expected})")


def validate_pickup(pickup_qty: int, heavy_soil_qty: int, damaged_qty: int) -> None:
    """Raises if Heavy Soil or Damaged qty exceeds pickup qty."""
    if heavy_soil_qty > pickup_qty:
        raise ValueError(f"Heavy Soil ({heavy_soil_qty}) cannot exceed pickup ({pickup_qty})")
    if damaged_qty > pickup_qty:
        raise ValueError(f"Damaged ({damaged_qty}) cannot exceed pickup ({pickup_qty})")


def check_photo_requirements(heavy_soil_qty: int, damaged_qty: int) -> int:
    """1 flagged unit = 1 photo. A unit logged under both needs 2 photos, so
    callers pass already-split counts: photos = heavy_soil_qty + damaged_qty."""
    if heavy_soil_qty < 0 or damaged_qty < 0:
        raise ValueError("Quantities cannot be negative")
    return heavy_soil_qty + damaged_qty


def can_end_trip(pending_hotels: list[str]) -> bool:
    """End Trip only after ALL hotels completed. A pending/skipped hotel blocks."""
    return len(pending_hotels) == 0


def pending_pickup_blocks_trip() -> bool:
    """A pending pickup does NOT block Trip completion. Separate from hotels."""
    return False


def can_edit_dc(otp_authenticated: bool, dc_generated: bool) -> str:
    """Edit policy for Return Fresh / DC quantities."""
    if otp_authenticated:
        return "blocked: OTP-authenticated DC is final and cannot be changed"
    if dc_generated:
        return "allowed_with_regeneration: update the quantity, regenerate the DC, complete the new OTP flow"
    return "allowed: edit before Save and Continue / DC generation"


# ---- LLM function-calling schemas (src/generate.py) ----
FUNCTION_DECLARATIONS = [
    {
        "name": "calculate_return_fresh",
        "description": "Compute Return Fresh as Expected minus Delivered. Call for ANY delivery-shortage quantity question. Never do the subtraction in prose.",
        "parameters": {
            "type": "object",
            "properties": {
                "expected": {"type": "integer", "description": "Expected Quantity"},
                "delivered": {"type": "integer", "description": "Delivered Quantity"},
            },
            "required": ["expected", "delivered"],
        },
    },
    {
        "name": "check_photo_requirements",
        "description": "Compute required photos: 1 per Heavy Soil unit + 1 per Damaged unit. A unit logged under both counts in both quantities.",
        "parameters": {
            "type": "object",
            "properties": {
                "heavy_soil_qty": {"type": "integer"},
                "damaged_qty": {"type": "integer"},
            },
            "required": ["heavy_soil_qty", "damaged_qty"],
        },
    },
    {
        "name": "validate_delivery",
        "description": "Check a delivery is legal (Delivered must not exceed Expected).",
        "parameters": {
            "type": "object",
            "properties": {
                "expected": {"type": "integer"},
                "delivered": {"type": "integer"},
            },
            "required": ["expected", "delivered"],
        },
    },
    {
        "name": "validate_pickup",
        "description": "Check pickup segregation quantities are legal (each must not exceed pickup qty).",
        "parameters": {
            "type": "object",
            "properties": {
                "pickup_qty": {"type": "integer"},
                "heavy_soil_qty": {"type": "integer"},
                "damaged_qty": {"type": "integer"},
            },
            "required": ["pickup_qty", "heavy_soil_qty", "damaged_qty"],
        },
    },
    {
        "name": "can_end_trip",
        "description": "Check whether End Trip is available given the list of still-pending hotels.",
        "parameters": {
            "type": "object",
            "properties": {
                "pending_hotels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names of incomplete/pending hotels (empty if none)",
                }
            },
            "required": ["pending_hotels"],
        },
    },
    {
        "name": "pending_pickup_blocks_trip",
        "description": "Ask whether a pending pickup blocks Trip completion (it does not).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "can_edit_dc",
        "description": "Ask whether Return Fresh / DC quantities can still be edited.",
        "parameters": {
            "type": "object",
            "properties": {
                "otp_authenticated": {"type": "boolean"},
                "dc_generated": {"type": "boolean"},
            },
            "required": ["otp_authenticated", "dc_generated"],
        },
    },
]

_FUNCTIONS = {
    "calculate_return_fresh": calculate_return_fresh,
    "check_photo_requirements": check_photo_requirements,
    "validate_delivery": validate_delivery,
    "validate_pickup": validate_pickup,
    "can_end_trip": can_end_trip,
    "pending_pickup_blocks_trip": pending_pickup_blocks_trip,
    "can_edit_dc": can_edit_dc,
}


def call_function(name: str, args: dict):
    """Execute a rules-engine function by name (used by generate.py tool loop)."""
    fn = _FUNCTIONS.get(name)
    if fn is None:
        raise ValueError(f"Unknown rule function: {name}")
    try:
        result = fn(**args)
        return {"ok": True, "result": result if result is not None else "valid"}
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
