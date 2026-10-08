"""Deterministic rules engine — Phase 4 (not yet implemented).

Pure functions, no LLM math. Exposed as tools in Phase 5:
- calculate_return_fresh(expected, delivered) -> int
- validate_delivery(expected, delivered) -> None (raises on over-delivery)
- validate_pickup(pickup_qty, heavy_soil_qty, damaged_qty) -> None
- check_photo_requirements(heavy_soil_qty, damaged_qty) -> int
- can_end_trip(pending_hotels) -> bool          (pending hotel BLOCKS)
- pending_pickup_blocks_trip() -> False         (pending pickup does NOT block)
- can_edit_dc(otp_authenticated, dc_generated) -> str
See SPEC §2 for the rule list.
"""
