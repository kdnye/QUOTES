# FSI Quote Tool — Spreadsheet Integration Guide

This guide shows you how to connect **Microsoft Excel** or **Google Sheets** directly to the FSI Quote Tool API so you can generate freight quotes in bulk without opening a browser.

---

## Before you start

You need one thing: **an API key**.

Your FSI account administrator issues API keys. Once you have one, you can view it any time by logging into the FSI Quote Tool and opening **Help → API Reference**.

> **Keep your key private.** Treat it like a password. Do not paste it into a shared cell or commit it to a shared document. If you think it has been exposed, ask your FSI admin to regenerate it.

---

## Which integration should I use?

| Your situation | Use this |
|---|---|
| Google Sheets | [Google Sheets — Apps Script](#google-sheets--apps-script) |
| Excel, macros allowed (no IT restriction) | [Excel — VBA](#excel--vba-macro) |
| Excel, macros blocked by IT policy | [Excel — Power Query](#excel--power-query-no-macros-needed) |

Not sure if macros are allowed? Try opening any `.xlsm` file. If you see a yellow bar that says "Enable Content" and clicking it works, macros are allowed.

---

## Spreadsheet layout

All three integrations expect the **same column order** by default. Set up your sheet with these headers in row 1:

### Input columns (you fill in)

| Column | Header | Notes |
|---|---|---|
| A | Quote Type | `Hotshot` or `Air` — capitalisation does not matter |
| B | Origin ZIP | 5-digit US ZIP. Format column as **Text** in Excel to preserve leading zeros |
| C | Destination ZIP | Same |
| D | Weight (lbs) | Actual shipment weight in pounds |
| E | Pieces | Number of units. Leave blank to default to 1 |
| F | Accessorials | Comma-separated extra services, e.g. `Liftgate, Residential Delivery`. Leave blank if none |
| G | Length (in) | Package length in inches — optional, used for dimensional weight |
| H | Width (in) | Package width in inches — optional |
| I | Height (in) | Package height in inches — optional |
| J | Dim Weight (lbs) | Pre-calculated dimensional weight — use **instead of** G/H/I, not alongside |

### Output columns (written by the integration — leave blank)

| Column | Header | Notes |
|---|---|---|
| K | Quote ID | Unique quote reference, e.g. `Q-BCDFGHJ2` |
| L | Total ($) | Final price including all charges |
| M | Weight Method | `Actual` — actual weight was used; `Dimensional` — dim weight was higher and used instead |
| N | Billable Weight | The weight the price was calculated on (greater of actual and dim) |
| O | Base Rate ($) | Freight base rate before surcharges |
| P | Accessorial Total ($) | Sum of all accessorial charges |
| Q | Zone | Rate zone, e.g. `C` |
| R | Miles | Calculated route distance |
| S | Status | `Success` or error detail |

Data starts in **row 2**. Row 1 is the header.

If your columns are in a different order, see [Changing column assignments](#changing-column-assignments).

---

## Dimensional weight

Billable weight is always the **greater of actual weight and dimensional weight**. The `Weight Method` output column tells you which was used — the API returns `Actual` or `Dimensional` (Proper case).

There are two ways to supply dimensional weight:

**Option 1 — Let the API calculate it from dimensions (columns G, H, I):**
Fill in Length, Width, and Height in inches. All three must be present or none will be sent. The API calculates dimensional weight using its internal divisor and returns both the calculated dim weight and the billable weight.

**Option 2 — Supply a pre-calculated dim weight (column J):**
If you already know the dimensional weight, enter it directly in column J. This skips the API's dimension calculation. Column J takes priority over G/H/I if both are filled.

**Neither supplied:** The API uses actual weight only — column J and G/H/I can be left blank.

---

## Cost breakdown fields

The API returns a price breakdown alongside the total. The output columns O and P expose the key components:

```
Total  =  Base Rate  +  Surcharges  +  Accessorial Total
```

Example for a 520 lb Hotshot quote, zone C, 1,142 miles:

| Field | Value |
|---|---|
| Base Rate | $680.00 |
| Surcharges | $167.50 |
| Liftgate accessorial | $75.00 |
| **Total** | **$922.50** |

> The breakdown is useful for auditing quotes and validating rate changes.

---

## Google Sheets — Apps Script

Apps Script is Google's built-in scripting platform. No add-ins or admin rights are required.

### One-time setup

1. Open your Google Sheet.
2. Click **Extensions > Apps Script**.
3. Delete all existing code in the editor.
4. Open `google_sheets/FSI_Quote_AppsScript.gs` from this package, copy all of its contents, and paste into the editor.
5. Click **Save** (Ctrl+S). If prompted, name the project `FSI Quote`.
6. Close the editor and **refresh** your Google Sheet (F5).
7. A new **FSI Quotes** menu now appears in the menu bar.
8. Click **FSI Quotes > Set API key**, paste your key, and click OK.

The key is stored in Script Properties — it is never visible in any cell.

### First run authorisation

The first time you use any menu item, Google will ask you to authorise the script. This is normal:

1. Click **Review permissions**.
2. Choose your Google account.
3. Click **Advanced > Go to FSI Quote (unsafe)** if the app has not been verified (this is expected for in-house scripts).
4. Click **Allow**.

You only need to do this once.

### Using it

**Single row:** Click any cell in a data row, then **FSI Quotes > Get quote — current row**.

**All rows:** **FSI Quotes > Process all rows** — the script works from row 2 down to the last non-empty row.

Results are written to columns K–S. A status of `Success` in column S means the quote was returned correctly. Any other value is an error message that explains what needs to be fixed.

---

## Excel — VBA Macro

This approach uses a macro that calls the API directly. No external libraries or add-ins are needed — it uses MSXML2, which ships with every Windows Office installation.

### One-time setup

1. **Save your workbook as `.xlsm`** (macro-enabled). Excel will prompt you if you try to save a macro in a `.xlsx` file.
2. Press **Alt+F11** to open the Visual Basic editor.
3. Click **Insert > Module**.
4. Open `excel/FSI_Quote_VBA.bas` from this package, copy all of its contents, and paste into the module window.
5. Find the line near the top:
   ```
   Private Const API_KEY  As String = "YOUR_API_KEY_HERE"
   ```
   Replace `YOUR_API_KEY_HERE` with your actual API key (keep the quotes).
6. Press **Alt+Q** to close the editor.

### Optional: add buttons

1. Click **Insert > Shapes** and draw a rectangle.
2. Type a label, e.g. `Get Quote`.
3. Right-click the shape > **Assign Macro** > select `GenerateFSIQuote` > OK.
4. Repeat with a second button assigned to `BatchGenerateFSIQuotes` for bulk runs.

### Using it

When you open the file, click **Enable Content** in the yellow bar if it appears.

**Single row:** Click any cell in a data row and run `GenerateFSIQuote` (from the button, or **Developer > Macros > GenerateFSIQuote > Run**).

**All rows:** Run `BatchGenerateFSIQuotes` to process every non-empty row from row 2 downward.

Results are written to columns K–S.

---

## Excel — Power Query (no macros needed)

Power Query is built into Excel 2016 and later. It does not require macros and does not require admin rights.

### One-time setup

#### Step 1 — Import the query function

1. Click the **Data** tab > **Get Data > Launch Power Query Editor**.
2. In the editor: **Home > New Source > Blank Query**.
3. In the new query, click **Home > Advanced Editor**.
4. Select all the existing text and delete it.
5. Open `excel/FSI_Quote_PowerQuery.m` from this package, copy all of its contents, and paste.
6. Click **Done**.
7. In the Queries panel on the left, rename the query `FSIQuote` (double-click to rename).
8. Click **Home > Close & Load > Close & Load To…** > select **Only Create Connection** > OK.

#### Step 2 — Set the privacy level (required)

Without this step Power Query will block the outbound connection.

1. In the Power Query Editor: **File > Options and Settings > Data Source Settings**.
2. Find the `https://quote.freightservices.net` entry and select it.
3. Click **Edit Permissions > Privacy Level > Organizational** (or **Public** if Organizational is not available).
4. Click **OK** and close settings.

#### Step 3 — Create your data query

1. Format your data as an Excel Table: select your data range, press **Ctrl+T**, tick "My table has headers".
2. In Power Query Editor: **New Source > From Table/Range** — select your table.
3. Add a Custom Column: **Add Column > Custom Column**. Name it `Quote Result` and use this formula:

   ```m
   = FSIQuote(
       "YOUR_API_KEY_HERE",
       [#"Quote Type"],
       [#"Origin ZIP"],
       [#"Destination ZIP"],
       [#"Weight (lbs)"],
       [Pieces],
       [Accessorials],
       [#"Length (in)"],
       [#"Width (in)"],
       [#"Height (in)"],
       [#"Dim Weight (lbs)"]
   )
   ```

   Replace `YOUR_API_KEY_HERE` with your actual key (keep the quotes).
   Dimension columns are optional — pass `null` for any you don't use:

   ```m
   = FSIQuote(
       "YOUR_API_KEY_HERE",
       [#"Quote Type"], [#"Origin ZIP"], [#"Destination ZIP"], [#"Weight (lbs)"],
       [Pieces], [Accessorials],
       null, null, null, null   // no dimensions
   )
   ```

4. Click the expand icon on the `Quote Result` column header (two arrows) and select the fields you want: `quote_id`, `total`, `weight_method`, `billable_weight`, `base_rate`, `accessorial_total`, `zone`, `miles`, `status`.
5. **Close & Load** — the results load into a new sheet.
6. To refresh quotes: click anywhere in the results table > **Data > Refresh All**.

> **API key security note:** In Power Query the key is stored in the query definition. To share the workbook without exposing the key, store the key in a named cell and reference it as `Excel.CurrentWorkbook(){[Name="APIKey"]}[Content]{0}[Column1]` instead of a literal string.

---

## Changing column assignments

If your sheet uses different column positions, edit the configuration section at the top of whichever file you installed.

**VBA** (in the module, below `Option Explicit`):
```vba
Private Const COL_QUOTE_TYPE  As String = "A"   ' change the letter
Private Const COL_LENGTH      As String = "G"   ' dim input columns can also be moved
' etc.
```

**Apps Script** (in the `CONFIG` block near the top):
```js
const CONFIG = {
  COL_QUOTE_TYPE:  1,   // 1 = column A, 2 = column B, etc.
  COL_LENGTH:      7,   // column G
  // etc.
};
```

**Power Query** — adjust the column names in the Custom Column formula to match your actual table header names.

---

## Accessorials reference

The API ignores unknown accessorial names, so a typo will silently produce a quote without that service. Use these exact names (capitalisation does not matter):

| Name | Description |
|---|---|
| `Liftgate` | Liftgate required at pickup or delivery |
| `Residential Delivery` | Delivery to a residential address |
| `Inside Delivery` | Carrier brings freight inside the building |
| `Appointment` | Scheduled delivery appointment required |
| `Notify Before Delivery` | Carrier calls ahead before arriving |

Check **Help → API Reference** in the Quote Tool for the current full list.

---

## Rate limits and batch tips

The API allows **30 requests per minute**.

- The Apps Script batch function automatically pauses for 2 seconds every 10 rows — no action needed.
- The VBA batch function does not pause. If you see `429 Too Many Requests` errors in the Status column, add a wait: in the `BatchGenerateFSIQuotes` sub, after `ProcessRow ws, r`, add `Application.Wait Now + TimeValue("00:00:02")`.
- Power Query runs all requests when you click Refresh. If you have more than 30 rows, consider refreshing in batches of 25–30.

---

## Common errors

| Status column shows | Cause | Fix |
|---|---|---|
| `Error: API key not configured` | Key placeholder not replaced | Edit the config section and paste your key |
| `HTTP 401` | Missing or malformed Authorization header | Check the key is correct and the word `Bearer` is included before it |
| `HTTP 403` | Key invalid, disabled, or not yet approved | Contact your FSI admin |
| `HTTP 400 — quote_type must be…` | Unrecognised quote type value | Use exactly `Hotshot` or `Air` |
| `HTTP 429` | Rate limit exceeded | Slow down batch processing (see above) |
| `Connection failed` | Network issue | Check internet access; try again |
| Origin/Destination ZIP error | Leading zero stripped by Excel | Format the ZIP column as **Text** before entering data |
| Dim weight not reflected | Only some of L/W/H filled | Provide all three dimensions, or use column J for a pre-calculated value |

---

## Email confirmation

The API can send a formatted quote summary to the email address on your FSI account after each successful quote. To enable it, add `"send_email": true` to the JSON request body.

- Works with per-user API keys only (not the global service token).
- The email is sent to the address associated with your API key — not to an address you supply in the request.
- The API returns an `"email_sent"` field alongside the quote result: `true` if the email was dispatched, `false` if it could not be sent (e.g. SMTP not configured, or email rate limit reached).

**VBA example** — add one line to the payload in `ProcessRow`:

```vba
payload = payload & ", ""send_email"": true}"
' Instead of:
' payload = payload & "}"
```

**Apps Script example** — add the field to the payload object in `callApi`:

```js
payload.send_email = true;
```

**Power Query example** — add `send_email = true` to the base record:

```m
baseRecord = [
    quote_type  = quoteType,
    origin      = originText,
    destination = destText,
    weight      = pWeight,
    pieces      = piecesNum,
    send_email  = true
],
```

---

## Questions?

Contact your **FSI account administrator** for API key issues.
Contact **FSI operations** for server-side errors (HTTP 500).
