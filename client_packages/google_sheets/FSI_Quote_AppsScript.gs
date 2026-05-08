// FSI Quote Tool — Google Sheets Integration
// -----------------------------------------------------------------------
// Requirements: None. Uses Google's built-in UrlFetchApp.
// Compatible with any Google Sheets account.
//
// SETUP (5 minutes):
//   1. Open your Google Sheet.
//   2. Extensions > Apps Script.
//   3. Delete any existing code and paste this entire file.
//   4. Click Save (Ctrl+S). Name the project "FSI Quote" if prompted.
//   5. Set your API key: click the gear icon (Project Settings) >
//      Script Properties > Add property:
//        Property name:  FSI_API_KEY
//        Value:          <your api key>
//      Save script properties.
//   6. Close the editor and refresh your Google Sheet.
//   7. A new "FSI Quotes" menu will appear. The first time you use it
//      Google will ask you to authorise the script — click through
//      (this is normal; the script only contacts freightservices.net).
//
// SPREADSHEET LAYOUT (default — change CONFIG values below to match yours):
//
//  Inputs
//   A  Quote Type       "Hotshot" or "Air" (case-insensitive)
//   B  Origin ZIP       5-digit
//   C  Destination ZIP  5-digit
//   D  Weight (lbs)     actual shipment weight
//   E  Pieces           number of units (blank = 1)
//   F  Accessorials     comma-separated, e.g. "Liftgate, Residential Delivery"
//   G  Length (in)      \
//   H  Width (in)        > optional; provide all three for the API to calc dim weight
//   I  Height (in)      /
//   J  Dim Weight (lbs) pre-calculated dim weight — supply instead of G/H/I
//
//  Outputs (written by the script)
//   K  Quote ID
//   L  Total ($)
//   M  Weight Method    "actual" or "dimensional"
//   N  Billable Weight  weight used for pricing
//   O  Base Rate ($)
//   P  Fuel Surcharge ($)
//   Q  Fuel %
//   R  VSC Surcharge ($)
//   S  Accessorial Total ($)
//   T  Zone
//   U  Miles
//   V  Status           "Success" or error detail
// -----------------------------------------------------------------------

const API_URL = 'https://quote.freightservices.net/api/quote';

// =====================================================================
// CONFIGURATION — change column numbers to match your sheet.
// A = 1, B = 2, C = 3, D = 4, E = 5, F = 6, G = 7, H = 8, I = 9 ...
// =====================================================================
const CONFIG = {
  // Input columns
  COL_QUOTE_TYPE:  1,   // A — "Hotshot" or "Air"
  COL_ORIGIN:      2,   // B — 5-digit origin ZIP
  COL_DEST:        3,   // C — 5-digit destination ZIP
  COL_WEIGHT:      4,   // D — actual shipment weight in lbs
  COL_PIECES:      5,   // E — number of pieces (blank = 1)
  COL_ACCESSORIAL: 6,   // F — comma-separated accessorials
  COL_LENGTH:      7,   // G — package length in inches (optional)
  COL_WIDTH:       8,   // H — package width in inches (optional)
  COL_HEIGHT:      9,   // I — package height in inches (optional)
  COL_DIM_WEIGHT:  10,  // J — pre-calculated dim weight in lbs (optional)
  //                         Supply dim weight OR length+width+height, not both.
  //                         Dim weight takes priority if both are filled.

  // Output columns
  COL_QUOTE_ID:    11,  // K — Quote ID
  COL_TOTAL:       12,  // L — Total price ($)
  COL_WT_METHOD:   13,  // M — "actual" or "dimensional"
  COL_BILL_WT:     14,  // N — Billable weight used for pricing
  COL_BASE_RATE:   15,  // O — Base rate ($)
  COL_FUEL_SURCH:  16,  // P — Fuel surcharge ($)
  COL_FUEL_PCT:    17,  // Q — Fuel surcharge rate (e.g. 0.15 = 15%)
  COL_VSC:         18,  // R — VSC surcharge ($)
  COL_ACC_TOTAL:   19,  // S — Total accessorial charges ($)
  COL_ZONE:        20,  // T — Rate zone
  COL_MILES:       21,  // U — Route distance in miles
  COL_STATUS:      22,  // V — "Success" or error detail

  DATA_START_ROW:  2,   // First row with data (row 1 is assumed to be the header)
};
// =====================================================================


// Adds the "FSI Quotes" menu when the spreadsheet opens.
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('FSI Quotes')
    .addItem('Get quote — current row', 'generateQuoteCurrentRow')
    .addItem('Process all rows', 'batchGenerateQuotes')
    .addSeparator()
    .addItem('Set API key', 'promptSetApiKey')
    .addToUi();
}


// Prompts the user to enter their API key and saves it in Script Properties.
// Script Properties are tied to the project, not the sheet, so the key is
// never visible in any spreadsheet cell.
function promptSetApiKey() {
  const ui = SpreadsheetApp.getUi();
  const response = ui.prompt(
    'Set FSI API Key',
    'Paste your API key below.\n\n' +
    'It will be stored securely in Script Properties and will not appear in any cell.',
    ui.ButtonSet.OK_CANCEL
  );
  if (response.getSelectedButton() === ui.Button.OK) {
    const key = response.getResponseText().trim();
    if (key.length === 0) {
      ui.alert('No key entered — nothing saved.');
      return;
    }
    PropertiesService.getScriptProperties().setProperty('FSI_API_KEY', key);
    ui.alert('API key saved successfully.');
  }
}


// Runs a quote for the row the user has selected.
function generateQuoteCurrentRow() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const row = sheet.getActiveCell().getRow();

  if (row < CONFIG.DATA_START_ROW) {
    SpreadsheetApp.getUi().alert(
      'Please click a data cell in row ' + CONFIG.DATA_START_ROW + ' or below first.'
    );
    return;
  }

  try {
    processRow(sheet, row);
  } catch (e) {
    sheet.getRange(row, CONFIG.COL_STATUS).setValue('Error: ' + e.message);
  }
}


// Runs quotes for every non-empty row from DATA_START_ROW to the last row.
// All input is read in a single getValues() call and all output is written
// in one setValues() call per output column regardless of row count.
function batchGenerateQuotes() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const lastRow = sheet.getLastRow();

  if (lastRow < CONFIG.DATA_START_ROW) {
    SpreadsheetApp.getUi().alert('No data rows found.');
    return;
  }

  const numRows = lastRow - CONFIG.DATA_START_ROW + 1;
  const numInputCols = Math.max(
    CONFIG.COL_QUOTE_TYPE, CONFIG.COL_ORIGIN, CONFIG.COL_DEST,
    CONFIG.COL_WEIGHT, CONFIG.COL_PIECES, CONFIG.COL_ACCESSORIAL,
    CONFIG.COL_LENGTH, CONFIG.COL_WIDTH, CONFIG.COL_HEIGHT, CONFIG.COL_DIM_WEIGHT
  );

  // Single read for all input rows
  const allValues = sheet.getRange(CONFIG.DATA_START_ROW, 1, numRows, numInputCols).getValues();

  // Output buffers — one entry per row, flushed in batch at the end
  const empty = () => Array(numRows).fill(null).map(() => ['']);
  const outQuoteId  = empty(), outTotal     = empty(), outWtMethod = empty();
  const outBillWt   = empty(), outBaseRate  = empty(), outFuelSurch = empty();
  const outFuelPct  = empty(), outVsc       = empty(), outAccTotal = empty();
  const outZone     = empty(), outMiles     = empty(), outStatus   = empty();

  let processed = 0;
  for (let i = 0; i < numRows; i++) {
    if (String(allValues[i][CONFIG.COL_QUOTE_TYPE - 1]).trim() === '') continue;

    try {
      const r = callApi(allValues[i]);
      outQuoteId[i]   = [r.quoteId];
      outTotal[i]     = [r.total];
      outWtMethod[i]  = [r.weightMethod];
      outBillWt[i]    = [r.billableWeight];
      outBaseRate[i]  = [r.baseRate];
      outFuelSurch[i] = [r.fuelSurcharge];
      outFuelPct[i]   = [r.fuelPct];
      outVsc[i]       = [r.vscSurcharge];
      outAccTotal[i]  = [r.accessorialTotal];
      outZone[i]      = [r.zone];
      outMiles[i]     = [r.miles];
      outStatus[i]    = [r.status];
    } catch (e) {
      outStatus[i] = ['Error: ' + e.message];
    }

    processed++;
    // Rate limit: API allows 30 requests per minute.
    // Brief pause every 10 rows keeps well within the limit.
    if (processed % 10 === 0) Utilities.sleep(2000);
  }

  // One write per output column covers all rows
  const sr = CONFIG.DATA_START_ROW;
  sheet.getRange(sr, CONFIG.COL_QUOTE_ID,   numRows, 1).setValues(outQuoteId);
  sheet.getRange(sr, CONFIG.COL_TOTAL,      numRows, 1).setValues(outTotal);
  sheet.getRange(sr, CONFIG.COL_WT_METHOD,  numRows, 1).setValues(outWtMethod);
  sheet.getRange(sr, CONFIG.COL_BILL_WT,    numRows, 1).setValues(outBillWt);
  sheet.getRange(sr, CONFIG.COL_BASE_RATE,  numRows, 1).setValues(outBaseRate);
  sheet.getRange(sr, CONFIG.COL_FUEL_SURCH, numRows, 1).setValues(outFuelSurch);
  sheet.getRange(sr, CONFIG.COL_FUEL_PCT,   numRows, 1).setValues(outFuelPct);
  sheet.getRange(sr, CONFIG.COL_VSC,        numRows, 1).setValues(outVsc);
  sheet.getRange(sr, CONFIG.COL_ACC_TOTAL,  numRows, 1).setValues(outAccTotal);
  sheet.getRange(sr, CONFIG.COL_ZONE,       numRows, 1).setValues(outZone);
  sheet.getRange(sr, CONFIG.COL_MILES,      numRows, 1).setValues(outMiles);
  sheet.getRange(sr, CONFIG.COL_STATUS,     numRows, 1).setValues(outStatus);

  SpreadsheetApp.getUi().alert('Done. ' + processed + ' row(s) processed.');
}


// -----------------------------------------------------------------------
// Internal: send the API request for one row and write results immediately.
// Used by generateQuoteCurrentRow for single-row operation.
// -----------------------------------------------------------------------
function processRow(sheet, r) {
  const numCols = Math.max(
    CONFIG.COL_QUOTE_TYPE, CONFIG.COL_ORIGIN, CONFIG.COL_DEST,
    CONFIG.COL_WEIGHT, CONFIG.COL_PIECES, CONFIG.COL_ACCESSORIAL,
    CONFIG.COL_LENGTH, CONFIG.COL_WIDTH, CONFIG.COL_HEIGHT, CONFIG.COL_DIM_WEIGHT
  );
  const values = sheet.getRange(r, 1, 1, numCols).getValues()[0];
  const res = callApi(values);
  sheet.getRange(r, CONFIG.COL_QUOTE_ID).setValue(res.quoteId);
  sheet.getRange(r, CONFIG.COL_TOTAL).setValue(res.total);
  sheet.getRange(r, CONFIG.COL_WT_METHOD).setValue(res.weightMethod);
  sheet.getRange(r, CONFIG.COL_BILL_WT).setValue(res.billableWeight);
  sheet.getRange(r, CONFIG.COL_BASE_RATE).setValue(res.baseRate);
  sheet.getRange(r, CONFIG.COL_FUEL_SURCH).setValue(res.fuelSurcharge);
  sheet.getRange(r, CONFIG.COL_FUEL_PCT).setValue(res.fuelPct);
  sheet.getRange(r, CONFIG.COL_VSC).setValue(res.vscSurcharge);
  sheet.getRange(r, CONFIG.COL_ACC_TOTAL).setValue(res.accessorialTotal);
  sheet.getRange(r, CONFIG.COL_ZONE).setValue(res.zone);
  sheet.getRange(r, CONFIG.COL_MILES).setValue(res.miles);
  sheet.getRange(r, CONFIG.COL_STATUS).setValue(res.status);
}


// -----------------------------------------------------------------------
// Internal: build payload from a pre-read row values array, POST to the
// API, and return a result object. Throws on validation errors.
// -----------------------------------------------------------------------
function callApi(values) {
  const apiKey = getApiKey();

  const quoteType = String(values[CONFIG.COL_QUOTE_TYPE - 1]).trim();
  const origin    = padZip(values[CONFIG.COL_ORIGIN - 1]);
  const dest      = padZip(values[CONFIG.COL_DEST - 1]);
  const weight    = Number(values[CONFIG.COL_WEIGHT - 1]);

  const piecesRaw = values[CONFIG.COL_PIECES - 1];
  const pieces    = (piecesRaw === '' || piecesRaw === null || piecesRaw === undefined)
                    ? 1
                    : parseInt(piecesRaw, 10);

  const accRaw = String(values[CONFIG.COL_ACCESSORIAL - 1]).trim();

  // Optional dimension inputs
  const lengthRaw   = values[CONFIG.COL_LENGTH    - 1];
  const widthRaw    = values[CONFIG.COL_WIDTH     - 1];
  const heightRaw   = values[CONFIG.COL_HEIGHT    - 1];
  const dimWtRaw    = values[CONFIG.COL_DIM_WEIGHT - 1];

  if (!quoteType) throw new Error('Quote Type is empty.');
  if (!origin || origin.length !== 5) throw new Error('Origin ZIP must be 5 digits.');
  if (!dest   || dest.length   !== 5) throw new Error('Destination ZIP must be 5 digits.');
  if (isNaN(weight) || weight <= 0)   throw new Error('Weight must be a positive number.');

  const payload = {
    quote_type:  normaliseQuoteType(quoteType),
    origin:      origin,
    destination: dest,
    weight:      weight,
    pieces:      pieces,
  };

  if (accRaw) {
    payload.accessorials = accRaw.split(',').map(s => s.trim()).filter(s => s.length > 0);
  }

  // Dimensions: dim_weight takes priority over L/W/H
  const dimWt = Number(dimWtRaw);
  if (dimWtRaw !== '' && dimWtRaw !== null && !isNaN(dimWt) && dimWt > 0) {
    payload.dim_weight = dimWt;
  } else {
    const len = Number(lengthRaw), wid = Number(widthRaw), ht = Number(heightRaw);
    const hasLWH = lengthRaw !== '' && widthRaw !== '' && heightRaw !== '' &&
                   !isNaN(len) && !isNaN(wid) && !isNaN(ht) &&
                   len > 0 && wid > 0 && ht > 0;
    if (hasLWH) {
      payload.length = len;
      payload.width  = wid;
      payload.height = ht;
    }
  }

  const options = {
    method:             'post',
    contentType:        'application/json',
    headers:            { Authorization: 'Bearer ' + apiKey },
    payload:            JSON.stringify(payload),
    muteHttpExceptions: true,
  };

  const response = UrlFetchApp.fetch(API_URL, options);
  const code     = response.getResponseCode();
  const data     = JSON.parse(response.getContentText());

  if (code === 201) {
    const meta = data.metadata || {};
    return {
      quoteId:          data.quote_id,
      total:            data.total,
      weightMethod:     data.weight_method     || '',
      billableWeight:   data.weight,
      baseRate:         meta.base_rate          != null ? meta.base_rate          : '',
      fuelSurcharge:    meta.fuel_surcharge     != null ? meta.fuel_surcharge     : '',
      fuelPct:          meta.fuel_pct           != null ? meta.fuel_pct           : '',
      vscSurcharge:     meta.vsc_surcharge      != null ? meta.vsc_surcharge      : '',
      accessorialTotal: meta.accessorial_total  != null ? meta.accessorial_total  : '',
      zone:             meta.zone               || '',
      miles:            meta.miles              != null ? meta.miles              : '',
      status:           'Success',
    };
  } else {
    const errMsg = data.remediation || ('HTTP ' + code);
    return {
      quoteId: '', total: '', weightMethod: '', billableWeight: '',
      baseRate: '', fuelSurcharge: '', fuelPct: '', vscSurcharge: '',
      accessorialTotal: '', zone: '', miles: '',
      status: 'Error: ' + errMsg,
    };
  }
}


// Retrieve API key from Script Properties.
function getApiKey() {
  const key = PropertiesService.getScriptProperties().getProperty('FSI_API_KEY');
  if (!key || key.trim() === '') {
    throw new Error('API key not set. Use FSI Quotes > Set API key to enter it.');
  }
  return key.trim();
}


// Zero-pad a ZIP code to exactly 5 characters.
// Handles both text ("01234") and numeric (1234) cell values.
function padZip(value) {
  return String(value).replace(/\.0+$/, '').padStart(5, '0').slice(0, 5);
}


// Accept "hotshot", "HOTSHOT", "Hotshot" — normalise to proper case.
function normaliseQuoteType(raw) {
  const lower = raw.toLowerCase();
  if (lower === 'hotshot') return 'Hotshot';
  if (lower === 'air')     return 'Air';
  return raw;  // pass through unknown values so the API returns the descriptive error
}
