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
// -----------------------------------------------------------------------

const API_URL = 'https://quote.freightservices.net/api/quote';

// =====================================================================
// CONFIGURATION — change column numbers to match your sheet.
// A = 1, B = 2, C = 3, D = 4, E = 5, F = 6, G = 7, H = 8, I = 9 ...
// =====================================================================
const CONFIG = {
  // Input columns
  COL_QUOTE_TYPE:  1,   // A — "Hotshot" or "Air" (case-insensitive)
  COL_ORIGIN:      2,   // B — 5-digit origin ZIP code
  COL_DEST:        3,   // C — 5-digit destination ZIP code
  COL_WEIGHT:      4,   // D — shipment weight in lbs
  COL_PIECES:      5,   // E — number of pieces (leave blank to default to 1)
  COL_ACCESSORIAL: 6,   // F — comma-separated, e.g. "Liftgate, Residential Delivery"

  // Output columns
  COL_QUOTE_ID:    7,   // G — Quote ID written by the script
  COL_TOTAL:       8,   // H — Total price written by the script
  COL_STATUS:      9,   // I — "Success" or error detail

  DATA_START_ROW:  2,   // First row containing data (row 1 is assumed to be the header)
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
function batchGenerateQuotes() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const lastRow = sheet.getLastRow();

  if (lastRow < CONFIG.DATA_START_ROW) {
    SpreadsheetApp.getUi().alert('No data rows found.');
    return;
  }

  let processed = 0;
  for (let r = CONFIG.DATA_START_ROW; r <= lastRow; r++) {
    const typeCell = sheet.getRange(r, CONFIG.COL_QUOTE_TYPE).getValue();
    if (String(typeCell).trim() === '') continue;  // skip blank rows

    try {
      processRow(sheet, r);
    } catch (e) {
      sheet.getRange(r, CONFIG.COL_STATUS).setValue('Error: ' + e.message);
    }

    processed++;
    // Rate limit: API allows 30 requests per minute.
    // Brief pause every 10 rows keeps well within limits.
    if (processed % 10 === 0) Utilities.sleep(2000);
  }

  SpreadsheetApp.getUi().alert('Done. ' + processed + ' row(s) processed.');
}


// -----------------------------------------------------------------------
// Internal: send the API request for one row and write results.
// -----------------------------------------------------------------------
function processRow(sheet, r) {
  const apiKey = getApiKey();

  // Read all input columns in one call for efficiency
  const numCols = Math.max(
    CONFIG.COL_QUOTE_TYPE, CONFIG.COL_ORIGIN, CONFIG.COL_DEST,
    CONFIG.COL_WEIGHT, CONFIG.COL_PIECES, CONFIG.COL_ACCESSORIAL
  );
  const values = sheet.getRange(r, 1, 1, numCols).getValues()[0];

  const quoteType = String(values[CONFIG.COL_QUOTE_TYPE - 1]).trim();
  const origin    = padZip(values[CONFIG.COL_ORIGIN - 1]);
  const dest      = padZip(values[CONFIG.COL_DEST - 1]);
  const weight    = Number(values[CONFIG.COL_WEIGHT - 1]);

  const piecesRaw = values[CONFIG.COL_PIECES - 1];
  const pieces    = (piecesRaw === '' || piecesRaw === null || piecesRaw === undefined)
                    ? 1
                    : parseInt(piecesRaw, 10);

  const accRaw = String(values[CONFIG.COL_ACCESSORIAL - 1]).trim();

  if (!quoteType) throw new Error('Quote Type is empty.');
  if (!origin || origin.length !== 5) throw new Error('Origin ZIP must be 5 digits.');
  if (!dest   || dest.length   !== 5) throw new Error('Destination ZIP must be 5 digits.');
  if (isNaN(weight) || weight <= 0)   throw new Error('Weight must be a positive number.');

  // Build payload
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

  // POST request
  const options = {
    method:           'post',
    contentType:      'application/json',
    headers:          { Authorization: 'Bearer ' + apiKey },
    payload:          JSON.stringify(payload),
    muteHttpExceptions: true,  // lets us read error bodies from the API
  };

  const response = UrlFetchApp.fetch(API_URL, options);
  const code     = response.getResponseCode();
  const data     = JSON.parse(response.getContentText());

  if (code === 201) {
    sheet.getRange(r, CONFIG.COL_QUOTE_ID).setValue(data.quote_id);
    sheet.getRange(r, CONFIG.COL_TOTAL).setValue(data.total);
    sheet.getRange(r, CONFIG.COL_STATUS).setValue('Success');
  } else {
    const errMsg = data.remediation || ('HTTP ' + code);
    sheet.getRange(r, CONFIG.COL_STATUS).setValue('Error: ' + errMsg);
  }
}


// Retrieve API key from Script Properties.
function getApiKey() {
  const key = PropertiesService.getScriptProperties().getProperty('FSI_API_KEY');
  if (!key || key.trim() === '') {
    throw new Error(
      'API key not set. Use FSI Quotes > Set API key to enter it.'
    );
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
