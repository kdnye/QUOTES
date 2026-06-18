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
5. Paste the contents of `FSI_ScienceCare_VBA.bas` into the module's code window. *(Don't paste an `Attribute VB_Name = "..."` line — that only works via File > Import, and VBA reports a syntax error on it when pasted.)*
6. Set `API_KEY` in **SECTION 1**.
7. (Optional) Verify cell addresses in **SECTION 2** against SHIPMENT 1.
8. **Alt+Q** to close. On each SHIPMENT tab: **Insert > Shapes**, draw a button, right-click > **Assign Macro > `RunScienceCareQuote`**.

> **Prefer importing?** Use **File > Import File…** and pick the `.bas` directly — the module name is set automatically. The paste flow above is what the rest of this README assumes.

The macro always operates on the **active sheet**, so the same button code works on every SHIPMENT tab.

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
| 4 Hour Delivery/Pick-Up Window | J3 | `PickUp 4 Hour Window (e.g 10:00-14:00)` |
| Special Pickup or Delivery Time | J4 | `Specific PickUp Time (e.g. Deliver at 9:30am)` |
| Afterhours Delivery/Pickup | J5 | `Delivery After Hours (17:01-07:59)` |
| Two-Man Team Required | J7 | `Two Man Delivery` |
| Liftgate Required | J8 | `Liftgate Delivery` |

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

| Cell | Content |
|---|---|
| **C40** | FS by Air — total price ($) |
| **B40** | Air status: `Success`, `Skipped: ...`, or error detail |
| **C41** | FS by Hot Shot — total price ($) |
| **E41** | Hot Shot Miles (returned by API) |
| **B41** | Hotshot status: `Success` or error detail |

> Writing to **C40 / C41 overwrites the existing in-sheet formulas** that compute the totals from the static rate charts. That is intentional — once the API parity is confirmed, the `Domestic Charts - FS`, `International Chart - FS`, and `HOTSHOT Pricing` tabs (and their hidden duplicates) can be removed.

---

## International shipments

If **B7** is non-empty, the macro writes `Skipped: international` to both status cells and shows a one-line dialog. The current FSI API endpoint is domestic-only. This guard will be removed once the API supports international Air.

---

## Customising cell references

All cell addresses live in `SECTION 2` of the `.bas` file. If the SHIPMENT 1 layout changes, update only that block — no hunting through the code.
