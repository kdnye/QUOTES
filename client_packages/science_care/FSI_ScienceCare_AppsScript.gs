// FSI Quote Tool — Science Care Integration (Google Sheets)
// -----------------------------------------------------------------------
// Reads the Science Care quote sheet and fires Air and Hotshot API
// requests simultaneously using UrlFetchApp.fetchAll(), then writes both
// results back to the sheet in a single pass.
//
// SETUP (10 minutes):
//   1. Open your Google Sheets copy of the Science Care quote form.
//   2. Extensions > Apps Script — delete existing code and paste this file.
//   3. Save (Ctrl+S). Refresh the sheet.
//   4. FSI Quotes (Science Care) > Set API key — paste your key.
//   5. Authorise when Google prompts (first run only).
//   6. Verify every row/column number in CONFIG against your sheet.
//   7. Fill in LAB_ZIP below with your actual lab codes and ZIPs.
//
// SPREADSHEET LAYOUT:
//   This script uses row/column numbers (1-based). Row 1 = top row.
//   Adjust CONFIG to match your sheet if the layout differs.
// -----------------------------------------------------------------------

const API_URL = 'https://quote.freightservices.net/api/quote';

// =====================================================================
// CONFIGURATION — adjust row/column numbers to match your sheet.
// Column A = 1, B = 2, C = 3 … H = 8, M = 13, etc.
// =====================================================================
const CONFIG = {
  // Header inputs
  ROW_SC_LAB:       3,   COL_SC_LAB:       2,   // B3  — SC Lab code
  ROW_DEST_ZIP:     5,   COL_DEST_ZIP:     2,   // B5  — US Zip Code (destination)

  // Accessorial Y/N markers (column H = 8)
  ROW_ACC_4HR_WINDOW:    2,   // H2  — 4 Hour Delivery/Pick-Up Window ($50)
  ROW_ACC_AFTERHOURS_DL: 3,   // H3  — Afterhours Delivery (+$110)
  ROW_ACC_WEEKEND_DL:    4,   // H4  — Weekend Delivery (+$125)
  ROW_ACC_SPECIAL_TIME:  5,   // H5  — Special Pickup or Delivery Time (+$95)
  ROW_ACC_AFTERHOURS_PU: 6,   // H6  — Afterhours Pickup — Returns Only (+$110)
  ROW_ACC_WEEKEND_PU:    7,   // H7  — Weekend Pickup — Returns Only (+$125)
  ROW_ACC_TWO_MAN:       8,   // H8  — Two-Man Team Required (+$125)
  ROW_ACC_LIFTGATE:      9,   // H9  — Liftgate Required (+$75)
  COL_ACCESSORIALS:      8,   // Column H for all accessorial Y/N cells

  // Box-type quantity cells (column A = 1)
  ROW_QTY_MEDIUM:       38,  COL_QTY: 1,   // A38 — Medium 20"x15"x18"
  ROW_QTY_LARGE:        39,               // A39 — Large 32"x18"x20"
  ROW_QTY_XLARGE:       40,               // A40 — X-Large 52"x20"x15"
  ROW_QTY_SM_AIRTRAY:   41,               // A41 — Small Airtray 60"x21"x12"
  ROW_QTY_AIRTRAY:      42,               // A42 — Airtray 79"x24"x15"
  ROW_QTY_WIDE_AIRTRAY: 43,               // A43 — Wide Airtray (dims unknown — excluded from calc)
  ROW_QTY_WIDE_SM:      44,               // A44 — Wide Airtray Small 60"x31"x19"

  // Shipment totals
  ROW_TOTAL_WEIGHT: 47,  COL_TOTAL_WEIGHT: 13,  // M47 — Total Shipment Weight (lbs)
  ROW_TOTAL_BOXES:  45,  COL_TOTAL_BOXES:   1,  // A45 — Total Boxes

  // Output cells
  ROW_OUT_AIR:    53,  COL_OUT_AIR_TOTAL:  2,  COL_OUT_AIR_STATUS:  3,   // B53, C53
  ROW_OUT_HOT:    54,  COL_OUT_HOT_TOTAL:  2,  COL_OUT_HOT_STATUS:  3,   // B54, C54
                       COL_OUT_HOT_MILES:  7,                             // G54

  // Standard US domestic dim divisor (lbs per cubic inch)
  DIM_DIVISOR: 139,
};

// Box dimensions [Length, Height, Width] in inches (from Science Care form header).
// Wide Airtray is excluded — its dimensions are not printed on the form.
const BOX_DIMS = {
  medium:       [20, 15, 18],
  large:        [32, 18, 20],
  xlarge:       [52, 20, 15],
  sm_airtray:   [60, 21, 12],
  airtray:      [79, 24, 15],
  wide_sm:      [60, 31, 19],
};
// =====================================================================


// =====================================================================
// LAB-TO-ZIP LOOKUP
// Add one entry per Science Care lab code your account uses.
// Verify every ZIP — the values below are examples only.
// =====================================================================
const LAB_ZIP = {
  'SCIL': '92618',   // Irvine, CA         — VERIFY
  'SCAZ': '85040',   // Phoenix, AZ        — VERIFY
  'SCFL': '32256',   // Jacksonville, FL   — VERIFY
  'SCGA': '30349',   // Atlanta, GA        — VERIFY
  'SCMD': '21042',   // Columbia, MD       — VERIFY
  'SCNJ': '08816',   // East Brunswick, NJ — VERIFY
  'SCTX': '77032',   // Houston, TX        — VERIFY
  'SCWA': '98188',   // Seattle, WA        — VERIFY
};

// =====================================================================
// ACCESSORIAL NAME MAPPING
// Maps each form row to the FSI API accessorial string.
// Confirm accepted names with your FSI account representative.
// =====================================================================
function accName(row) {
  const map = {
    [CONFIG.ROW_ACC_4HR_WINDOW]:    '4 Hour Window',
    [CONFIG.ROW_ACC_AFTERHOURS_DL]: 'Afterhours Delivery',
    [CONFIG.ROW_ACC_WEEKEND_DL]:    'Weekend Delivery',
    [CONFIG.ROW_ACC_SPECIAL_TIME]:  'Special Delivery Time',
    [CONFIG.ROW_ACC_AFTERHOURS_PU]: 'Afterhours Pickup',
    [CONFIG.ROW_ACC_WEEKEND_PU]:    'Weekend Pickup',
    [CONFIG.ROW_ACC_TWO_MAN]:       'Two-Man Team',
    [CONFIG.ROW_ACC_LIFTGATE]:      'Liftgate',
  };
  return map[row] || '';
}


// -----------------------------------------------------------------------
// MENU
// -----------------------------------------------------------------------
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('FSI Quotes (Science Care)')
    .addItem('Run Air + Hotshot quote', 'runScienceCareQuote')
    .addSeparator()
    .addItem('Set API key', 'promptSetApiKey')
    .addToUi();
}

function promptSetApiKey() {
  const ui = SpreadsheetApp.getUi();
  const r = ui.prompt(
    'Set FSI API Key',
    'Paste your FSI API key. Stored in Script Properties — never in a cell.',
    ui.ButtonSet.OK_CANCEL
  );
  if (r.getSelectedButton() === ui.Button.OK) {
    const key = r.getResponseText().trim();
    if (!key) { ui.alert('No key entered — nothing saved.'); return; }
    PropertiesService.getScriptProperties().setProperty('FSI_API_KEY', key);
    ui.alert('API key saved.');
  }
}

function getApiKey() {
  const key = PropertiesService.getScriptProperties().getProperty('FSI_API_KEY');
  if (!key || !key.trim()) throw new Error('API key not set. Use FSI Quotes (Science Care) > Set API key.');
  return key.trim();
}


// -----------------------------------------------------------------------
// MAIN ENTRY POINT
// -----------------------------------------------------------------------
function runScienceCareQuote() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const apiKey = getApiKey();

  // --- Inputs ---
  const labCode  = String(sheet.getRange(CONFIG.ROW_SC_LAB, CONFIG.COL_SC_LAB).getValue()).trim().toUpperCase();
  const destRaw  = sheet.getRange(CONFIG.ROW_DEST_ZIP, CONFIG.COL_DEST_ZIP).getValue();
  const destZip  = padZip(destRaw);
  const originZip = LAB_ZIP[labCode];

  if (!originZip) {
    SpreadsheetApp.getUi().alert(
      'Unknown lab code "' + labCode + '".\nAdd it to the LAB_ZIP table in the script.'
    );
    return;
  }
  if (!destZip || destZip.length !== 5) {
    SpreadsheetApp.getUi().alert('US Zip Code cell is empty or invalid.');
    return;
  }

  const totalWeight = Number(sheet.getRange(CONFIG.ROW_TOTAL_WEIGHT, CONFIG.COL_TOTAL_WEIGHT).getValue());
  if (!totalWeight || totalWeight <= 0) {
    SpreadsheetApp.getUi().alert('Total Shipment Weight must be greater than 0.');
    return;
  }

  const boxesRaw = sheet.getRange(CONFIG.ROW_TOTAL_BOXES, CONFIG.COL_TOTAL_BOXES).getValue();
  const pieces   = (boxesRaw === '' || boxesRaw == null) ? 1 : parseInt(boxesRaw, 10);

  const dimWeight    = calcDimWeight(sheet);
  const accessorials = readAccessorials(sheet);

  // --- Build shared payload ---
  const base = {
    origin:      originZip,
    destination: destZip,
    weight:      totalWeight,
    pieces:      pieces,
  };
  if (dimWeight > 0)          base.dim_weight    = dimWeight;
  if (accessorials.length > 0) base.accessorials = accessorials;

  const airPayload = Object.assign({ quote_type: 'Air'     }, base);
  const hotPayload = Object.assign({ quote_type: 'Hotshot' }, base);

  const options = (payload) => ({
    method:             'post',
    contentType:        'application/json',
    headers:            { Authorization: 'Bearer ' + apiKey },
    payload:            JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  // --- Fire both requests simultaneously ---
  let responses;
  try {
    responses = UrlFetchApp.fetchAll([
      { url: API_URL, ...options(airPayload) },
      { url: API_URL, ...options(hotPayload) },
    ]);
  } catch (e) {
    SpreadsheetApp.getUi().alert('Network error: ' + e.message);
    return;
  }

  // --- Write Air result ---
  writeResult(sheet, responses[0], 'Air');

  // --- Write Hotshot result ---
  writeResult(sheet, responses[1], 'Hotshot');

  SpreadsheetApp.getUi().alert('Done. Air and Hotshot quotes updated.');
}


// -----------------------------------------------------------------------
// calcDimWeight — sum (L × H × W × qty) / DIM_DIVISOR for each box type.
// -----------------------------------------------------------------------
function calcDimWeight(sheet) {
  const boxRows = [
    { row: CONFIG.ROW_QTY_MEDIUM,       dims: BOX_DIMS.medium       },
    { row: CONFIG.ROW_QTY_LARGE,        dims: BOX_DIMS.large        },
    { row: CONFIG.ROW_QTY_XLARGE,       dims: BOX_DIMS.xlarge       },
    { row: CONFIG.ROW_QTY_SM_AIRTRAY,   dims: BOX_DIMS.sm_airtray   },
    { row: CONFIG.ROW_QTY_AIRTRAY,      dims: BOX_DIMS.airtray      },
    { row: CONFIG.ROW_QTY_WIDE_AIRTRAY, dims: null                  }, // dims unknown
    { row: CONFIG.ROW_QTY_WIDE_SM,      dims: BOX_DIMS.wide_sm      },
  ];

  let total = 0;
  for (const { row, dims } of boxRows) {
    if (!dims) continue;
    const qty = Number(sheet.getRange(row, CONFIG.COL_QTY).getValue());
    if (qty > 0) {
      total += (dims[0] * dims[1] * dims[2] * qty) / CONFIG.DIM_DIVISOR;
    }
  }
  return total;
}


// -----------------------------------------------------------------------
// readAccessorials — collect Y-marked rows into an API string array.
// -----------------------------------------------------------------------
function readAccessorials(sheet) {
  const accRows = [
    CONFIG.ROW_ACC_4HR_WINDOW,
    CONFIG.ROW_ACC_AFTERHOURS_DL,
    CONFIG.ROW_ACC_WEEKEND_DL,
    CONFIG.ROW_ACC_SPECIAL_TIME,
    CONFIG.ROW_ACC_AFTERHOURS_PU,
    CONFIG.ROW_ACC_WEEKEND_PU,
    CONFIG.ROW_ACC_TWO_MAN,
    CONFIG.ROW_ACC_LIFTGATE,
  ];

  const result = [];
  for (const row of accRows) {
    const val = String(sheet.getRange(row, CONFIG.COL_ACCESSORIALS).getValue()).trim().toUpperCase();
    if (val === 'Y') {
      const name = accName(row);
      if (name) result.push(name);
    }
  }
  return result;
}


// -----------------------------------------------------------------------
// writeResult — parse one API response and write to the appropriate cells.
// -----------------------------------------------------------------------
function writeResult(sheet, response, quoteType) {
  const code = response.getResponseCode();
  let data;
  try {
    data = JSON.parse(response.getContentText());
  } catch (_) {
    data = {};
  }

  const isAir = quoteType === 'Air';

  if (code === 201) {
    const total = data.total != null ? data.total : '';
    const meta  = data.metadata || {};
    const miles = meta.miles    != null ? meta.miles : '';

    if (isAir) {
      sheet.getRange(CONFIG.ROW_OUT_AIR, CONFIG.COL_OUT_AIR_TOTAL).setValue(total);
      sheet.getRange(CONFIG.ROW_OUT_AIR, CONFIG.COL_OUT_AIR_STATUS).setValue('Success');
    } else {
      sheet.getRange(CONFIG.ROW_OUT_HOT, CONFIG.COL_OUT_HOT_TOTAL).setValue(total);
      sheet.getRange(CONFIG.ROW_OUT_HOT, CONFIG.COL_OUT_HOT_STATUS).setValue('Success');
      sheet.getRange(CONFIG.ROW_OUT_HOT, CONFIG.COL_OUT_HOT_MILES).setValue(miles);
    }
  } else {
    const errMsg = data.remediation || ('HTTP ' + code);
    if (isAir) {
      sheet.getRange(CONFIG.ROW_OUT_AIR, CONFIG.COL_OUT_AIR_STATUS).setValue('Error: ' + errMsg);
    } else {
      sheet.getRange(CONFIG.ROW_OUT_HOT, CONFIG.COL_OUT_HOT_STATUS).setValue('Error: ' + errMsg);
    }
  }
}


// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------
function padZip(value) {
  return String(value).replace(/\.0+$/, '').padStart(5, '0').slice(0, 5);
}
