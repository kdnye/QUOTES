"""Tests for destination-zone to PADD region resolution."""

from app.services.fuel_surcharge import NATIONAL_REGION, resolve_padd_region


def test_resolve_padd_region_supports_zero_padded_zone_codes() -> None:
    """Map zero-padded destination zones to canonical numeric keys.

    Inputs:
        dest_zone: A destination zone string that may include leading zeros.
        zones_config: Dict from ``vsc_zones`` app setting.

    Outputs:
        Returns the mapped PADD region string for the canonical zone key.

    External dependencies:
        Calls ``app.services.fuel_surcharge.resolve_padd_region`` only.
    """

    zones_config = {"9": "PADD4", "10": "PADD5"}

    assert resolve_padd_region("09", zones_config) == "PADD4"


def test_resolve_padd_region_falls_back_to_national_when_missing() -> None:
    """Fallback to NATIONAL when mapping cannot resolve a destination zone.

    Inputs:
        dest_zone: A zone value not present in the mapping.
        zones_config: Dict from ``vsc_zones`` app setting.

    Outputs:
        Returns ``NATIONAL`` when there is no matching configured key.

    External dependencies:
        Calls ``app.services.fuel_surcharge.resolve_padd_region`` only.
    """

    zones_config = {"9": "PADD4", "10": "PADD5"}

    assert resolve_padd_region("99", zones_config) == NATIONAL_REGION
