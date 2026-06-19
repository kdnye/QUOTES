"""Shared numeric constants for the quoting pipeline.

A single import target for values that previously appeared as repeated
magic numbers across services, routes, and the Science Care
orchestrator. Keep this file free of imports from other ``app``
modules so it can be pulled in anywhere without circular-import risk.
"""

from __future__ import annotations

# Standard FSI dimensional-weight divisor (cubic inches per pound).
# Matches the value used by the SC workbook macro so the web page and
# the legacy spreadsheet produce identical dim weights. Sourced from
# the carrier's published rule and intentionally hard-coded - this is
# a rate-engine constant, not a tunable.
DIM_DIVISOR: int = 166
