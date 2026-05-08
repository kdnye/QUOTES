// FSI Quote Tool — Google Sheets Integration (INTERNAL)
// -----------------------------------------------------------------------
// INTERNAL USE ONLY. This version includes fuel_surcharge, fuel_pct, and
// vsc_surcharge in the output. The client-facing version
// (google_sheets/FSI_Quote_AppsScript.gs) omits those columns —
// do not distribute this file externally.
// -----------------------------------------------------------------------
// Requirements: None. Uses Google's built-in UrlFetchApp.
//
// SETUP: same as the client package. Set your API key via
// FSI Quotes > Set API key. The script stores it in Script Properties.
//
// SPREADSHEET LAYOUT (default):
//
//  Inputs (A–J): same as client package
//
//  Outputs (written by the script)
//   K  Quote ID
//   L  Total ($)
//   M  Weight Method    "Actual" or "Dimensional"
//   N  Billable Weight
//   O  Base Rate ($)
//   P  Fuel Surcharge ($)   <-- internal only
//   Q  Fuel %               <-- internal only
//   R  VSC Surcharge ($)    <-- internal only
//   S  Accessorial Total ($)
//   T  Zone
//   U  Miles
//   V  Status
// -----------------------------------------------------------------------

const API_URL = 'https://quote.freightservices.net/api/quote';

// =====================================================================
// CONFIGURATION
// =====================================================================
const CONFIG = {
  // Input columns
  COL_QUOTE_TYPE:  1,
  COL_ORIGIN:      2,
  COL_DEST:        3,
  COL_WEIGHT:      4,
  COL_PIECES:      5,
  COL_ACCESSORIAL: 6,
  COL_LENGTH:      7,
  COL_WIDTH:       8,
  COL_HEIGHT:      9,
  COL_DIM_WEIGHT:  10,

  // Output columns — full breakdown including internal fields
  COL_QUOTE_ID:    11,  // K
  COL_TOTAL:       12,  // L
  COL_WT_METHOD:   13,  // M
  COL_BILL_WT:     14,  // N
  COL_BASE_RATE:   15,  // O
  COL_FUEL_SURCH:  16,  // P  <-- internal only
  COL_FUEL_PCT:    17,  // Q  <-- internal only
  COL_VSC:         18,  // R  <-- internal only
  COL_ACC_TOTAL:   19,  // S
  COL_ZONE:        20,  // T
  COL_MILES:       21,  // U
  COL_STATUS:      22,  // V

  DATA_START_ROW:  2,
};
// =====================================================================


function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('FSI Quotes (Internal)')
    .addItem('Get quote — current row', 'generateQuoteCurrentRow')
    .addItem('Process all rows', 'batchGenerateQuotes')
    .addSeparator()
    .addItem('Set API key', 'promptSetApiKey')
    .addToUi();
}


function promptSetApiKey() {
  const ui = SpreadsheetApp.getUi();
  const response = ui.prompt(
    'Set FSI API Key',
    'Paste your API key below.\n\nStored in Script Properties — not visible in any cell.',
    ui.ButtonSet.OK_CANCEL
  );
  if (response.getSelectedButton() === ui.Button.OK) {
    const key = response.getResponseText().trim();
    if (key.length === 0) { ui.alert('No key entered — nothing saved.'); return; }
    PropertiesService.getScriptProperties().setProperty('FSI_API_KEY', key);
    ui.alert('API key saved successfully.');
  }
}


function generateQuoteCurrentRow() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const row = sheet.getActiveCell().getRow();
  if (row < CONFIG.DATA_START_ROW) {
    SpreadsheetApp.getUi().alert('Please click a data cell in row ' + CONFIG.DATA_START_ROW + ' or below first.');
    return;
  }
  try {
    processRow(sheet, row);
  } catch (e) {
    sheet.getRange(row, CONFIG.COL_STATUS).setValue('Error: ' + e.message);
  }
}


function batchGenerateQuotes() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const lastRow = sheet.getLastRow();
  if (lastRow < CONFIG.DATA_START_ROW) { SpreadsheetApp.getUi().alert('No data rows found.'); return; }

  const numRows = lastRow - CONFIG.DATA_START_ROW + 1;
  const numInputCols = Math.max(
    CONFIG.COL_QUOTE_TYPE, CONFIG.COL_ORIGIN, CONFIG.COL_DEST,
    CONFIG.COL_WEIGHT, CONFIG.COL_PIECES, CONFIG.COL_ACCESSORIAL,
    CONFIG.COL_LENGTH, CONFIG.COL_WIDTH, CONFIG.COL_HEIGHT, CONFIG.COL_DIM_WEIGHT
  );

  const allValues = sheet.getRange(CONFIG.DATA_START_ROW, 1, numRows, numInputCols).getValues();

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
    if (processed % 10 === 0) Utilities.sleep(2000);
  }

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


function callApi(values) {
  const apiKey = getApiKey();

  const quoteType = String(values[CONFIG.COL_QUOTE_TYPE - 1]).trim();
  const origin    = padZip(values[CONFIG.COL_ORIGIN - 1]);
  const dest      = padZip(values[CONFIG.COL_DEST - 1]);
  const weight    = Number(values[CONFIG.COL_WEIGHT - 1]);
  const piecesRaw = values[CONFIG.COL_PIECES - 1];
  const pieces    = (piecesRaw === '' || piecesRaw === null || piecesRaw === undefined) ? 1 : parseInt(piecesRaw, 10);
  const accRaw    = String(values[CONFIG.COL_ACCESSORIAL - 1]).trim();
  const lengthRaw = values[CONFIG.COL_LENGTH    - 1];
  const widthRaw  = values[CONFIG.COL_WIDTH     - 1];
  const heightRaw = values[CONFIG.COL_HEIGHT    - 1];
  const dimWtRaw  = values[CONFIG.COL_DIM_WEIGHT - 1];

  if (!quoteType) throw new Error('Quote Type is empty.');
  if (!origin || origin.length !== 5) throw new Error('Origin ZIP must be 5 digits.');
  if (!dest   || dest.length   !== 5) throw new Error('Destination ZIP must be 5 digits.');
  if (isNaN(weight) || weight <= 0)   throw new Error('Weight must be a positive number.');

  const payload = { quote_type: normaliseQuoteType(quoteType), origin, destination: dest, weight, pieces };
  if (accRaw) payload.accessorials = accRaw.split(',').map(s => s.trim()).filter(s => s.length > 0);

  const dimWt = Number(dimWtRaw);
  if (dimWtRaw !== '' && dimWtRaw !== null && !isNaN(dimWt) && dimWt > 0) {
    payload.dim_weight = dimWt;
  } else {
    const len = Number(lengthRaw), wid = Number(widthRaw), ht = Number(heightRaw);
    if (lengthRaw !== '' && widthRaw !== '' && heightRaw !== '' && !isNaN(len) && !isNaN(wid) && !isNaN(ht) && len > 0 && wid > 0 && ht > 0) {
      payload.length = len; payload.width = wid; payload.height = ht;
    }
  }

  const response = UrlFetchApp.fetch(API_URL, {
    method: 'post', contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + apiKey },
    payload: JSON.stringify(payload), muteHttpExceptions: true,
  });
  const code = response.getResponseCode();
  const data = JSON.parse(response.getContentText());

  if (code === 201) {
    const meta    = data.metadata || {};
    const details = meta.details  || {};
    return {
      quoteId:          data.quote_id,
      total:            data.total,
      weightMethod:     data.weight_method                          || '',
      billableWeight:   data.weight,
      zone:             data.zone                                   || '',
      baseRate:         details.base_rate       != null ? details.base_rate       : '',
      fuelSurcharge:    details.fuel_surcharge  != null ? details.fuel_surcharge  : '',
      fuelPct:          details.fuel_pct        != null ? details.fuel_pct        : '',
      vscSurcharge:     details.vsc_surcharge   != null ? details.vsc_surcharge   : '',
      accessorialTotal: meta.accessorial_total  != null ? meta.accessorial_total  : '',
      miles:            meta.miles              != null ? meta.miles              : '',
      status:           'Success',
    };
  } else {
    const errMsg = data.remediation || ('HTTP ' + code);
    return { quoteId: '', total: '', weightMethod: '', billableWeight: '', zone: '',
             baseRate: '', fuelSurcharge: '', fuelPct: '', vscSurcharge: '',
             accessorialTotal: '', miles: '', status: 'Error: ' + errMsg };
  }
}


function getApiKey() {
  const key = PropertiesService.getScriptProperties().getProperty('FSI_API_KEY');
  if (!key || key.trim() === '') throw new Error('API key not set. Use FSI Quotes (Internal) > Set API key.');
  return key.trim();
}

function padZip(value) {
  return String(value).replace(/\.0+$/, '').padStart(5, '0').slice(0, 5);
}

function normaliseQuoteType(raw) {
  const lower = raw.toLowerCase();
  if (lower === 'hotshot') return 'Hotshot';
  if (lower === 'air')     return 'Air';
  return raw;
}
