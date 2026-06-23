# FSI Quote Tool — Science Care 3_MASTER Integration

Wires each `SHIPMENT` tab in `3_MASTER_TOOL_2026` to the FSI Quote API. One button press calls the API twice (Air, then Hotshot) and writes both totals into the `FS by Air` and `FS by Hot Shot` rows.

This module replaces the in-sheet VLOOKUPs against `Domestic Charts - FS`, `International Chart - FS`, and `HOTSHOT Pricing` for domestic shipments. International shipments are skipped until the API supports them.

> **SHIPMENT 1 is the source of truth.** Cell addresses in the module match the SHIPMENT 1 layout. The other SHIPMENT tabs are being brought into the same layout — once aligned, the same macro works on every tab.

---

## Which file to use

| Environment | File |
|---|---|
| Excel (.xlsm) | `FSI_ScienceCare_VBA.bas` |
| Google Sheets | `FSI_ScienceCare_AppsScript.gs` *(uses older form layout — needs the same update before use)* |

---

## Excel — VBA setup

1. Save `3_MASTER_TOOL_2026.xlsm` as `.xlsm` if it isn't already.
2. **Alt+F11** to open the VBA editor.
3. **Insert > Module**.
4. With the new `Module1` selected, press **F4** to open the Properties pane and change `(Name)` to `FSI_ScienceCare`.
5. Paste the contents of `FSI_ScienceCare_VBA.bas` into the module's code window.
6. Set `API_KEY` in **SECTION 1**.
7. (Optional) Verify cell addresses in **SECTION 2** against SHIPMENT 1.
8. **Alt+Q** to close. Two buttons to wire up:
   - On each SHIPMENT tab: **Insert > Shapes**, draw a button, right-click > **Assign Macro > `RunScienceCareQuote`** — quotes that single tab.
   - On any tab (or in the ribbon): another button assigned to **`RunAllShipmentQuotes`** — quotes every SHIPMENT 1–7 in one click and reports a summary at the end.

> **Prefer importing?** Use **File > Import File…** and pick `FSI_ScienceCare_VBA.bas` directly. The file no longer carries an `Attribute VB_Name` header, so VBA names the module after the file's base name (`FSI_ScienceCare_VBA`) — rename it to `FSI_ScienceCare` in the Properties pane (F4) after the import.

`RunScienceCareQuote` always operates on the **active sheet**, so the same button code works on every SHIPMENT tab. `RunAllShipmentQuotes` walks `SHIPMENT 1` through `SHIPMENT N` (N = `SHIPMENT_TAB_COUNT` in SECTION 2, default `7`) — it suppresses per-tab popups, disables screen redraws while running, and shows one summary popup at the end listing the outcome for each tab.

> **Why isn't it truly parallel?** VBA is single-threaded; there's no equivalent of Apps Script's `UrlFetchApp.fetchAll()`. The batch runner fires the requests back-to-back as fast as the API responds (typically ~1s each), so seven tabs × two quotes each completes in roughly 10–15 seconds.

---

## Origin ZIP — resolved from the workbook's own lookup

The lab code in **B4** (e.g. `SCCA`) is translated to a 5-digit origin ZIP by looking it up in **`Drop downs OTH - SC`** column A → column B. This is the same table the existing in-sheet formula at row 67 uses, so no separate VBA mapping is maintained — add a new lab by adding a row to that sheet.

If the lookup sheet is missing or the lab code is not in the table, the macro writes an error to the status cell and exits cleanly.

---

## Inputs read from the active SHIPMENT tab

| Field | Cell | Notes |
|---|---|---|
| SC Lab code | **B4** | Looked up in `Drop downs OTH - SC` → origin ZIP |
| Destination ZIP | **B5** | 5-digit US ZIP |
| International country | **B7** | If filled, macro skips with a warning (API is domestic-only) |
| Total shipment weight (lbs) | **I37** | Used as `weight` |
| Total boxes | **A30** | Used as `pieces` (defaults to 1 if empty) |
| Box quantities (for dim weight) | **A26 / A27 / A28 / A29** | Medium / Large / X-Large / Airtray |
| Accessorials (Y/N markers) | **J3, J4, J5, J7, J8** | See mapping below |

`J6` (Weekend) and `J9` (VSC) are intentionally not sent: VSC is computed server-side from the lane zone, and Weekend has no current API equivalent.

---

## Accessorial mapping

| Form label | Cell | API string sent |
|---|---|---|
| 4 Hour Delivery/Pick-Up Window | J3 | `4hr Window` |
| Special Pickup or Delivery Time | J4 | `Less than 4 hrs` |
| Afterhours Delivery/Pickup | J5 | `After Hours` |
| Two-Man Team Required | J7 | `Two Man` |
| Liftgate Required | J8 | `Liftgate` |

A cell is treated as active when it contains the letter `Y` (case-insensitive).

---

## Dimensional weight

Combined dim weight is calculated across all four box types with a quantity:

```
dim_weight = Σ (L × H × W × qty) / 166   for each box type
```

| Box type | Qty cell | L | H | W |
|---|---|---|---|---|
| Medium  | A26 | 20 | 15 | 18 |
| Large   | A27 | 32 | 18 | 20 |
| X-Large | A28 | 52 | 20 | 15 |
| Airtray | A29 | 79 | 24 | 15 |

The API compares the dim weight against the actual weight in `I37` and prices on whichever is greater. The response's `weight_method` confirms which was used. (Not surfaced on the sheet today — can be added on request.)

---

## Outputs written to the sheet

Per-tab outputs (each SHIPMENT N):

| Cell | Content |
|---|---|
| **C40** | FS by Air — total price ($) |
| **B40** | Air status: `Success`, `Skipped: ...`, or error detail |
| **C41** | FS by Hot Shot — total price ($) |
| **H41** | Hot Shot Miles (returned by API) |
| **B41** | Hotshot status: `Success` or error detail |
| **A53** | Origin ZIP shipment note (from `metadata.origin_notes`); empty when the ZIP has no configured note |
| **B53** | Destination ZIP shipment note (from `metadata.dest_notes`) |

> The notes match what the FSI web UI surfaces under "QUOTE RESULT". Row 53 can be hidden — the user typically exposes both notes via a concatenated formula in another cell (e.g. `A43`) so the notes display next to the shipment header while the source cells stay out of sight.

Summary rollup on **SHIPMENT 1** (rewritten by the macro after every quote):

| Cell | Content |
|---|---|
| **C44** | Cheapest of SHIPMENT 1's `{C40 Air, C41 Hotshot, C42 Established Lane}` |
| **C45–C50** | Same calculation for SHIPMENT 2 through SHIPMENT 7 |
| **C51** | Grand total — sum of `C44:C50` (row computed dynamically as `SUMMARY_FIRST_ROW + SHIPMENT_TAB_COUNT`, so bumping the tab count shifts the total down rather than overwriting the last shipment row) |

`CheapestFreight` skips zero, blank, error, `"N/A"`, and non-numeric values — so a skipped international shipment or a missing established-lane row contributes 0 instead of breaking the row. The macro overwrites the legacy in-sheet formulas that broke when the static rate-chart tabs were removed.

**SC-to-SC routing** (mirrors 3_MASTER's pre-API branch): when a SHIPMENT tab's `B9` ("SHIPMENT TYPE") reads `SC to SC`, the rollup uses that tab's **Established Lane (C42)** price instead of cheapest-of-three — because SC-to-SC moves are pre-negotiated lab-to-lab lanes. If `C42` is `"N/A"` for that tab, the row falls back to cheapest of `{Air, Hotshot}` so it still contributes something to the total. Any other value in `B9` (blank, `Outbound`, `Inbound`) uses the standard cheapest-of-three logic. SHIPMENT 1 leaves `B9` blank, so the SC-to-SC branch never fires on row C44.

> Writing to **C40 / C41 overwrites the existing in-sheet formulas** that compute the totals from the static rate charts. That is intentional — once the API parity is confirmed, the `Domestic Charts - FS`, `International Chart - FS`, and `HOTSHOT Pricing` tabs (and their hidden duplicates) can be removed.

---

## International shipments

If **B7** is non-empty, the macro writes `Skipped: international` to both status cells and shows a one-line dialog. The current FSI API endpoint is domestic-only. This guard will be removed once the API supports international Air.

---

## Customising cell references

All cell addresses live in `SECTION 2` of the `.bas` file. If the SHIPMENT 1 layout changes, update only that block — no hunting through the code.
