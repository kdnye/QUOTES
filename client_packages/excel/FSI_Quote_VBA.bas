Attribute VB_Name = "FSI_Quote"
' FSI Quote Tool — Excel VBA Integration
' -----------------------------------------------------------------------
' Requirements: None. Uses MSXML2.XMLHTTP which ships with Windows/Office.
' Compatible with: Excel 2016+ on Windows (32-bit or 64-bit).
'
' SETUP (5 minutes):
'   1. Save your workbook as .xlsm (macro-enabled workbook).
'   2. Press Alt+F11 to open the VBA editor.
'   3. Click Insert > Module and paste this entire file.
'   4. Replace YOUR_API_KEY_HERE below with your actual key.
'   5. Adjust the column letters in the CONFIGURATION section if needed.
'   6. Close the editor (Alt+Q).
'   7. Optional: Insert > Shapes, draw a button, right-click > Assign Macro
'      > GenerateFSIQuote for single-row, or BatchGenerateFSIQuotes for all rows.
' -----------------------------------------------------------------------

Option Explicit

' =====================================================================
' CONFIGURATION — update these to match your spreadsheet
' =====================================================================
Private Const API_KEY  As String = "YOUR_API_KEY_HERE"
Private Const API_URL  As String = "https://quote.freightservices.net/api/quote"

' Input column letters (A, B, C, ...)
Private Const COL_QUOTE_TYPE  As String = "A"   ' "Hotshot" or "Air"
Private Const COL_ORIGIN      As String = "B"   ' 5-digit origin ZIP code
Private Const COL_DEST        As String = "C"   ' 5-digit destination ZIP code
Private Const COL_WEIGHT      As String = "D"   ' Shipment weight in lbs
Private Const COL_PIECES      As String = "E"   ' Number of pieces (blank = 1)
Private Const COL_ACCESSORIAL As String = "F"   ' Comma-separated, e.g. "Liftgate, Residential Delivery"

' Output column letters
Private Const COL_QUOTE_ID    As String = "G"   ' Quote ID returned by API
Private Const COL_TOTAL       As String = "H"   ' Total price ($)
Private Const COL_STATUS      As String = "I"   ' "Success" or error detail
' =====================================================================


' Run a quote for whichever row is currently selected.
Public Sub GenerateFSIQuote()
    Dim r As Long
    r = ActiveCell.Row
    If r < 2 Then
        MsgBox "Click a data row (row 2 or below) first.", vbExclamation, "FSI Quote"
        Exit Sub
    End If
    ProcessRow ActiveSheet, r
End Sub


' Run quotes for every non-empty row from row 2 to the last row.
Public Sub BatchGenerateFSIQuotes()
    Dim ws As Worksheet
    Set ws = ActiveSheet

    Dim lastRow As Long
    lastRow = ws.Cells(ws.Rows.Count, COL_QUOTE_TYPE).End(xlUp).Row
    If lastRow < 2 Then
        MsgBox "No data found below the header row.", vbInformation, "FSI Quote"
        Exit Sub
    End If

    Dim r As Long, processed As Long
    processed = 0
    For r = 2 To lastRow
        If Trim(CStr(ws.Cells(r, COL_QUOTE_TYPE).Value)) <> "" Then
            ProcessRow ws, r
            processed = processed + 1
        End If
    Next r

    MsgBox "Finished. " & processed & " row(s) processed.", vbInformation, "FSI Quote"
End Sub


' -----------------------------------------------------------------------
' Internal: send the API request and write results for one row.
' -----------------------------------------------------------------------
Private Sub ProcessRow(ws As Worksheet, r As Long)
    ' Guard: API key must be set
    If API_KEY = "YOUR_API_KEY_HERE" Or Len(Trim(API_KEY)) = 0 Then
        ws.Cells(r, COL_STATUS).Value = "Error: API key not configured. Edit FSI_Quote module."
        Exit Sub
    End If

    ' Read inputs
    Dim quoteType As String
    Dim origin    As String
    Dim dest      As String
    Dim weight    As Double
    Dim pieces    As Long
    Dim accStr    As String

    quoteType = Trim(CStr(ws.Cells(r, COL_QUOTE_TYPE).Value))
    origin    = FormatZip(ws.Cells(r, COL_ORIGIN).Value)
    dest      = FormatZip(ws.Cells(r, COL_DEST).Value)

    On Error GoTo InputError
    weight = CDbl(ws.Cells(r, COL_WEIGHT).Value)
    On Error GoTo 0

    Dim pVal As Variant
    pVal = ws.Cells(r, COL_PIECES).Value
    pieces = IIf(IsEmpty(pVal) Or CStr(pVal) = "", 1, CLng(pVal))

    accStr = Trim(CStr(ws.Cells(r, COL_ACCESSORIAL).Value))

    ' Build JSON payload manually (no external library needed)
    Dim payload As String
    payload = "{" & _
        """quote_type"": """ & quoteType & """, " & _
        """origin"": """ & origin & """, " & _
        """destination"": """ & dest & """, " & _
        """weight"": " & weight & ", " & _
        """pieces"": " & pieces

    If Len(accStr) > 0 Then
        payload = payload & ", ""accessorials"": [" & BuildAccJsonArray(accStr) & "]"
    End If
    payload = payload & "}"

    ' Send HTTP request
    Dim http As Object
    Set http = CreateObject("MSXML2.XMLHTTP")

    On Error GoTo ConnectionError
    http.Open "POST", API_URL, False
    http.setRequestHeader "Authorization", "Bearer " & API_KEY
    http.setRequestHeader "Content-Type", "application/json"
    http.send payload
    On Error GoTo 0

    ' Parse response (regex, no external parser needed)
    Dim resp As String
    resp = http.responseText

    Dim rx As Object
    Set rx = CreateObject("VBScript.RegExp")
    rx.Global = True

    Dim m As Object

    If http.Status = 201 Then
        rx.Pattern = """quote_id"":\s*""([^""]+)"""
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_QUOTE_ID).Value = m(0).SubMatches(0)

        rx.Pattern = """total"":\s*([\d\.]+)"
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_TOTAL).Value = CDbl(m(0).SubMatches(0))

        ws.Cells(r, COL_STATUS).Value = "Success"
    Else
        rx.Pattern = """remediation"":\s*""([^""]+)"""
        Set m = rx.Execute(resp)
        If m.Count > 0 Then
            ws.Cells(r, COL_STATUS).Value = "Error: " & m(0).SubMatches(0)
        Else
            ws.Cells(r, COL_STATUS).Value = "HTTP " & http.Status & " — " & http.statusText
        End If
    End If
    Exit Sub

InputError:
    ws.Cells(r, COL_STATUS).Value = "Error: Could not read Weight — check cell is a number."
    Exit Sub

ConnectionError:
    ws.Cells(r, COL_STATUS).Value = "Connection failed: " & Err.Description
End Sub


' Zero-pad a ZIP code to 5 digits, stripping any decimal Excel adds.
Private Function FormatZip(v As Variant) As String
    Dim s As String
    s = CStr(v)
    Dim dot As Long
    dot = InStr(s, ".")
    If dot > 0 Then s = Left(s, dot - 1)
    Do While Len(s) < 5
        s = "0" & s
    Loop
    FormatZip = Left(s, 5)
End Function


' Convert "Liftgate, Residential Delivery" → "Liftgate", "Residential Delivery"
Private Function BuildAccJsonArray(csv As String) As String
    Dim parts() As String
    parts = Split(csv, ",")
    Dim i As Long, result As String
    result = ""
    For i = 0 To UBound(parts)
        If i > 0 Then result = result & ", "
        result = result & """" & Trim(parts(i)) & """"
    Next i
    BuildAccJsonArray = result
End Function
