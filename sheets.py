import time
from datetime import datetime

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Colours — dark green theme
DARK_GREEN = {"red": 0.063, "green": 0.176, "blue": 0.071}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
LIGHT_GREEN = {"red": 0.878, "green": 0.961, "blue": 0.925}
MID_GREY = {"red": 0.95, "green": 0.95, "blue": 0.95}


def get_credentials() -> Credentials:
    info = dict(st.secrets["gcp_service_account"])
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def get_client() -> gspread.Client:
    return gspread.authorize(get_credentials())


def create_sheet(sheet_name: str, dataframes: dict, sheet_id: str, progress_cb=None) -> str:
    """
    Write issue DataFrames into an existing Google Sheet (owned by the user).
    Clears all existing worksheets and rebuilds from scratch.
    Returns the spreadsheet URL.
    """
    client = get_client()

    # ── Open existing spreadsheet by ID ────────────────────────────────────
    if progress_cb:
        progress_cb("📊 Opening Google Sheet…")

    sh = client.open_by_key(sheet_id)
    time.sleep(0.5)

    # Clear all existing worksheets so we start fresh
    existing = sh.worksheets()
    # Must keep at least one sheet — add a temp sheet first
    temp = sh.add_worksheet(title="_temp", rows="1", cols="1")
    for ws in existing:
        try:
            sh.del_worksheet(ws)
        except Exception:
            pass
    time.sleep(0.5)

    # ── Build issue tabs ────────────────────────────────────────────────────
    tab_info = []   # [(tab_name, row_count, gid)]
    skipped = []    # tabs that failed

    try:
        for display_name, df in dataframes.items():
            if progress_cb:
                progress_cb(f"📋 Writing: {display_name} ({len(df):,} rows)…")

            tab_name = display_name[:99]

            try:
                try:
                    ws = sh.add_worksheet(
                        title=tab_name,
                        rows=str(len(df) + 5),
                        cols=str(max(len(df.columns), 5)),
                    )
                except gspread.exceptions.APIError:
                    ws = sh.worksheet(tab_name)
                    ws.clear()

                headers = df.columns.tolist()
                rows = [headers] + _df_to_rows(df)

                # Write in chunks if large (Sheets API limit ~10MB per request)
                chunk_size = 5000
                for i in range(0, len(rows), chunk_size):
                    chunk = rows[i:i + chunk_size]
                    start_row = i + 1
                    ws.update(chunk, f"A{start_row}")
                    if len(rows) > chunk_size:
                        time.sleep(0.5)

                _format_header(sh, ws, len(headers))
                tab_info.append((tab_name, len(df), ws.id))

            except Exception as e:
                skipped.append(f"{tab_name} ({e})")

            time.sleep(1.2)

        # ── Cover page ──────────────────────────────────────────────────────
        if progress_cb:
            progress_cb("🏠 Building cover page…")
        _create_cover(sh, sheet_name, tab_info)

    finally:
        # Always clean up _temp regardless of what happened
        try:
            sh.del_worksheet(sh.worksheet("_temp"))
        except Exception:
            pass

    if skipped and progress_cb:
        progress_cb(f"⚠️ Skipped {len(skipped)} tab(s): {', '.join(skipped)}")

    if not tab_info:
        raise RuntimeError("No tabs were created successfully.")

    return f"https://docs.google.com/spreadsheets/d/{sh.id}"


# ── Private helpers ──────────────────────────────────────────────────────────

def _df_to_rows(df: pd.DataFrame) -> list:
    rows = []
    for _, row in df.iterrows():
        clean = []
        for v in row.tolist():
            if v is None or (isinstance(v, float) and pd.isna(v)):
                clean.append("")
            else:
                clean.append(str(v))
        rows.append(clean)
    return rows


def _format_header(sh: gspread.Spreadsheet, ws: gspread.Worksheet, num_cols: int):
    end_col = _col_letter(num_cols)
    try:
        sh.batch_update({
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": ws.id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": num_cols,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": DARK_GREEN,
                                "textFormat": {
                                    "foregroundColor": WHITE,
                                    "bold": True,
                                },
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }
                },
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": ws.id,
                            "gridProperties": {"frozenRowCount": 1},
                        },
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
            ]
        })
    except Exception:
        pass
    time.sleep(0.3)


def _create_cover(sh: gspread.Spreadsheet, sheet_name: str, tab_info: list):
    cover = sh.add_worksheet(title="Cover", rows="300", cols="10")

    # Move Cover to position 0
    try:
        all_ws = sh.worksheets()
        ordered = [cover] + [w for w in all_ws if w.id != cover.id]
        sh.reorder_worksheets(ordered)
    except Exception:
        pass

    timestamp = datetime.now().strftime("%d %b %Y at %H:%M")
    total_urls = sum(c for _, c, _ in tab_info)

    rows = [
        [sheet_name],
        [f"Exported {timestamp}  ·  {len(tab_info)} issue types  ·  {total_urls:,} affected URLs"],
        [""],
        ["Issue", "Affected URLs", ""],
    ]

    for tab_name, count, gid in tab_info:
        link = f'=HYPERLINK("#gid={gid}","→ View tab")'
        rows.append([tab_name, count, link])

    cover.update(rows, "A1")

    # Format cover
    try:
        sh.batch_update({"requests": [
            # Title — big dark green
            {"repeatCell": {
                "range": {"sheetId": cover.id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 3},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"fontSize": 18, "bold": True, "foregroundColor": DARK_GREEN},
                }},
                "fields": "userEnteredFormat.textFormat",
            }},
            # Subtitle
            {"repeatCell": {
                "range": {"sheetId": cover.id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 3},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"fontSize": 10, "foregroundColor": {"red": 0.4, "green": 0.4, "blue": 0.4}},
                }},
                "fields": "userEnteredFormat.textFormat",
            }},
            # Table header row
            {"repeatCell": {
                "range": {"sheetId": cover.id, "startRowIndex": 3, "endRowIndex": 4, "startColumnIndex": 0, "endColumnIndex": 3},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": DARK_GREEN,
                    "textFormat": {"bold": True, "foregroundColor": WHITE},
                }},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }},
            # Alternate row shading
            {"repeatCell": {
                "range": {"sheetId": cover.id, "startRowIndex": 4, "endRowIndex": 4 + len(tab_info), "startColumnIndex": 0, "endColumnIndex": 3},
                "cell": {"userEnteredFormat": {"backgroundColor": MID_GREY}},
                "fields": "userEnteredFormat.backgroundColor",
            }},
            # Column A width
            {"updateDimensionProperties": {
                "range": {"sheetId": cover.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 320},
                "fields": "pixelSize",
            }},
            # Column B width
            {"updateDimensionProperties": {
                "range": {"sheetId": cover.id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
                "properties": {"pixelSize": 140},
                "fields": "pixelSize",
            }},
            # Column C width
            {"updateDimensionProperties": {
                "range": {"sheetId": cover.id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
                "properties": {"pixelSize": 110},
                "fields": "pixelSize",
            }},
        ]})
    except Exception:
        pass


def _col_letter(n: int) -> str:
    result = ""
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result
