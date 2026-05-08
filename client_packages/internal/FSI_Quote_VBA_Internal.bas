Attribute VB_Name = "FSI_Quote_Internal"
' FSI Quote Tool — Excel VBA Integration (INTERNAL)
' -----------------------------------------------------------------------
' INTERNAL USE ONLY. This version includes fuel surcharge, fuel rate, and
' VSC surcharge in the output. The client-facing version (excel/FSI_Quote_VBA.bas)
' omits those columns — do not distribute this file externally.
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
'
' SPREADSHEET LAYOUT (default):
'
'  Inputs
'   A  Quote Type      "Hotshot" or "Air"
'   B  Origin ZIP      5-digit
'   C  Destination ZIP 5-digit
'   D  Weight (lbs)    actual shipment weight
'   E  Pieces          number of units (blank = 1)
'   F  Accessorials    comma-separated, e.g. "Liftgate, Residential Delivery"
'   G  Length (in)     \
'   H  Width (in)       > optional dimensions; all three required for dim-weight calc
'   I  Height (in)     /
'   J  Dim Weight (lbs) pre-calculated dim weight — supply instead of G/H/I
'
'  Outputs (written by the macro)
'   K  Quote ID
'   L  Total ($)
'   M  Weight Method   "Actual" or "Dimensional"
'   N  Billable Weight weight used for pricing
'   O  Base Rate ($)
'   P  Fuel Surcharge ($)   <-- internal only
'   Q  Fuel %               <-- internal only
'   R  VSC Surcharge ($)    <-- internal only
'   S  Accessorial Total ($)
'   T  Zone
'   U  Miles
'   V  Status          "Success" or error detail
' -----------------------------------------------------------------------

Option Explicit

' =====================================================================
' CONFIGURATION — update these to match your spreadsheet
' =====================================================================
Private Const API_KEY  As String = "YOUR_API_KEY_HERE"
Private Const API_URL  As String = "https://quote.freightservices.net/api/quote"

' Input column letters
Private Const COL_QUOTE_TYPE  As String = "A"
Private Const COL_ORIGIN      As String = "B"
Private Const COL_DEST        As String = "C"
Private Const COL_WEIGHT      As String = "D"
Private Const COL_PIECES      As String = "E"
Private Const COL_ACCESSORIAL As String = "F"
Private Const COL_LENGTH      As String = "G"
Private Const COL_WIDTH       As String = "H"
Private Const COL_HEIGHT      As String = "I"
Private Const COL_DIM_WEIGHT  As String = "J"

' Output column letters
Private Const COL_QUOTE_ID    As String = "K"
Private Const COL_TOTAL       As String = "L"
Private Const COL_WT_METHOD   As String = "M"
Private Const COL_BILL_WT     As String = "N"
Private Const COL_BASE_RATE   As String = "O"
Private Const COL_FUEL_SURCH  As String = "P"   ' internal: not in client package
Private Const COL_FUEL_PCT    As String = "Q"   ' internal: not in client package
Private Const COL_VSC         As String = "R"   ' internal: not in client package
Private Const COL_ACC_TOTAL   As String = "S"
Private Const COL_ZONE        As String = "T"
Private Const COL_MILES       As String = "U"
Private Const COL_STATUS      As String = "V"
' =====================================================================


Public Sub GenerateFSIQuote()
    Dim r As Long
    r = ActiveCell.Row
    If r < 2 Then
        MsgBox "Click a data row (row 2 or below) first.", vbExclamation, "FSI Quote"
        Exit Sub
    End If
    ProcessRow ActiveSheet, r
End Sub


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
            DoEvents
        End If
    Next r

    MsgBox "Finished. " & processed & " row(s) processed.", vbInformation, "FSI Quote"
End Sub


Private Sub ProcessRow(ws As Worksheet, r As Long)
    If API_KEY = "YOUR_API_KEY_HERE" Or Len(Trim(API_KEY)) = 0 Then
        ws.Cells(r, COL_STATUS).Value = "Error: API key not configured. Edit FSI_Quote_Internal module."
        Exit Sub
    End If

    Dim quoteType As String, origin As String, dest As String
    Dim weight As Double, pieces As Long, accStr As String

    quoteType = Trim(CStr(ws.Cells(r, COL_QUOTE_TYPE).Value))
    origin    = FormatZip(ws.Cells(r, COL_ORIGIN).Value)
    dest      = FormatZip(ws.Cells(r, COL_DEST).Value)

    On Error GoTo InputError
    weight = CDbl(ws.Cells(r, COL_WEIGHT).Value)

    Dim pVal As Variant
    pVal = ws.Cells(r, COL_PIECES).Value
    If IsEmpty(pVal) Or CStr(pVal) = "" Then
        pieces = 1
    Else
        pieces = CLng(pVal)
    End If
    On Error GoTo 0

    accStr = Trim(CStr(ws.Cells(r, COL_ACCESSORIAL).Value))

    Dim payload As String
    payload = "{" & _
        """quote_type"": """ & EscapeJson(quoteType) & """, " & _
        """origin"": """ & EscapeJson(origin) & """, " & _
        """destination"": """ & EscapeJson(dest) & """, " & _
        """weight"": " & Trim(Str(weight)) & ", " & _
        """pieces"": " & pieces

    If Len(accStr) > 0 Then
        payload = payload & ", ""accessorials"": [" & BuildAccJsonArray(accStr) & "]"
    End If

    Dim dimWtRaw As Variant
    dimWtRaw = ws.Cells(r, COL_DIM_WEIGHT).Value
    If Not (IsEmpty(dimWtRaw) Or CStr(dimWtRaw) = "") Then
        If Not IsNumeric(dimWtRaw) Then
            ws.Cells(r, COL_STATUS).Value = "Error: Dim Weight must be a number."
            Exit Sub
        End If
        payload = payload & ", ""dim_weight"": " & Trim(Str(CDbl(dimWtRaw)))
    Else
        Dim lenRaw As Variant, widRaw As Variant, htRaw As Variant
        lenRaw = ws.Cells(r, COL_LENGTH).Value
        widRaw = ws.Cells(r, COL_WIDTH).Value
        htRaw  = ws.Cells(r, COL_HEIGHT).Value
        If Not (IsEmpty(lenRaw) Or CStr(lenRaw) = "") And _
           Not (IsEmpty(widRaw) Or CStr(widRaw) = "") And _
           Not (IsEmpty(htRaw)  Or CStr(htRaw)  = "") Then
            If Not IsNumeric(lenRaw) Or Not IsNumeric(widRaw) Or Not IsNumeric(htRaw) Then
                ws.Cells(r, COL_STATUS).Value = "Error: Length, Width, and Height must all be numbers."
                Exit Sub
            End If
            payload = payload & _
                ", ""length"": " & Trim(Str(CDbl(lenRaw))) & _
                ", ""width"": "  & Trim(Str(CDbl(widRaw))) & _
                ", ""height"": " & Trim(Str(CDbl(htRaw)))
        End If
    End If

    payload = payload & "}"

    Dim http As Object
    Set http = CreateObject("MSXML2.XMLHTTP")

    On Error GoTo ConnectionError
    http.Open "POST", API_URL, False
    http.setRequestHeader "Authorization", "Bearer " & API_KEY
    http.setRequestHeader "Content-Type", "application/json"
    http.send payload
    On Error GoTo 0

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
        If m.Count > 0 Then ws.Cells(r, COL_TOTAL).Value = Val(m(0).SubMatches(0))

        rx.Pattern = """weight_method"":\s*""([^""]+)"""
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_WT_METHOD).Value = m(0).SubMatches(0)

        rx.Pattern = """weight"":\s*([\d\.]+)"
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_BILL_WT).Value = Val(m(0).SubMatches(0))

        rx.Pattern = """zone"":\s*""([^""]+)"""
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_ZONE).Value = m(0).SubMatches(0)

        ' base_rate / fuel_surcharge / fuel_pct / vsc_surcharge live inside
        ' metadata.details; regex scans the full string so depth does not matter.
        rx.Pattern = """base_rate"":\s*([\d\.]+)"
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_BASE_RATE).Value = Val(m(0).SubMatches(0))

        rx.Pattern = """fuel_surcharge"":\s*([\d\.]+)"
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_FUEL_SURCH).Value = Val(m(0).SubMatches(0))

        rx.Pattern = """fuel_pct"":\s*([\d\.]+)"
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_FUEL_PCT).Value = Val(m(0).SubMatches(0))

        rx.Pattern = """vsc_surcharge"":\s*([\d\.]+)"
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_VSC).Value = Val(m(0).SubMatches(0))

        rx.Pattern = """accessorial_total"":\s*([\d\.]+)"
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_ACC_TOTAL).Value = Val(m(0).SubMatches(0))

        rx.Pattern = """miles"":\s*([\d\.]+)"
        Set m = rx.Execute(resp)
        If m.Count > 0 Then ws.Cells(r, COL_MILES).Value = Val(m(0).SubMatches(0))

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
    ws.Cells(r, COL_STATUS).Value = "Error: Could not read numeric input — check Weight, Pieces, and Dimension cells."
    Exit Sub

ConnectionError:
    ws.Cells(r, COL_STATUS).Value = "Connection failed: " & Err.Description
End Sub


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

Private Function EscapeJson(s As String) As String
    EscapeJson = Replace(Replace(s, "\", "\\"), """", "\""")
End Function

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
