"""
Google Sheets storage for the food tracker.

The spreadsheet has two tabs, both created automatically on first use:

  Log           one row per food item (the "database")
  Daily Totals  a QUERY formula that rolls the Log up into one row per day

Env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON   the full contents of the service-account key file
  SHEET_ID                      the long ID in the sheet's URL
                                (docs.google.com/spreadsheets/d/<SHEET_ID>/edit)

If neither is set and FAKE_SHEET=1, an in-memory store is used instead so you
can try the app locally without Google set up (data is lost on restart).
"""
import datetime
import json
import os
import threading

LOG_TAB = "Log"
TOTALS_TAB = "Daily Totals"

# Column order in the Log tab. Change with care: Daily Totals depends on it.
COLUMNS = [
    "Entry ID",      # A
    "Date",          # B  yyyy-mm-dd, the phone's local date
    "Time",          # C  the phone's local time
    "Meal",          # D
    "Food",          # E
    "Portion",       # F
    "Calories",      # G
    "Protein (g)",   # H
    "Carbs (g)",     # I
    "Fat (g)",       # J
    "Fiber (g)",     # K
    "Sugar (g)",     # L
    "Sodium (mg)",   # M
    "Spoken Text",   # N
    "Logged At (UTC)",  # O
]

# Item keys (from the API / AI) that map onto the numeric columns above.
NUTRIENT_KEYS = ["calories", "protein_g", "carbs_g", "fat_g",
                 "fiber_g", "sugar_g", "sodium_mg"]

TOTALS_FORMULA = (
    "=QUERY(Log!B:M,"
    "\"select B, count(E), sum(G), sum(H), sum(I), sum(J), sum(K), sum(L), sum(M) "
    "where B is not null group by B order by B desc "
    "label count(E) 'Items', sum(G) 'Calories', sum(H) 'Protein (g)', "
    "sum(I) 'Carbs (g)', sum(J) 'Fat (g)', sum(K) 'Fiber (g)', "
    "sum(L) 'Sugar (g)', sum(M) 'Sodium (mg)'\",1)"
)


def _text(v):
    """Rows are written USER_ENTERED, so stop text that starts with = + - @
    from being treated as a formula."""
    s = str(v or "")
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def item_to_row(entry_id, date, time, item, transcript, logged_at):
    return [
        entry_id, date, time,
        _text(item.get("meal", "")),
        _text(item.get("food", "")),
        _text(item.get("portion", "")),
        *[_num(item.get(k)) for k in NUTRIENT_KEYS],
        _text(transcript),
        logged_at,
    ]


def row_to_item(row):
    row = list(row) + [""] * (len(COLUMNS) - len(row))
    item = {
        "id": str(row[0]),
        "date": _date_str(row[1]),
        "time": str(row[2]),
        "meal": str(row[3]),
        "food": str(row[4]),
        "portion": str(row[5]),
    }
    for i, k in enumerate(NUTRIENT_KEYS):
        item[k] = _num(row[6 + i])
    return item


def _num(v):
    try:
        n = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0
    return int(n) if n.is_integer() else round(n, 1)


def _date_str(v):
    """Normalise whatever Sheets hands back into yyyy-mm-dd."""
    if isinstance(v, (int, float)):  # serial number (days since 1899-12-30)
        return (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))).isoformat()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return s


class SheetStore:
    def __init__(self, sheet_id, service_account_info):
        import gspread
        self._gc = gspread.service_account_from_dict(service_account_info)
        self._sheet_id = sheet_id
        self._log = None
        self._lock = threading.Lock()

    def _log_ws(self):
        """Open the Log tab, creating both tabs the first time."""
        if self._log is not None:
            return self._log
        import gspread
        with self._lock:
            if self._log is not None:
                return self._log
            sh = self._gc.open_by_key(self._sheet_id)
            try:
                ws = sh.worksheet(LOG_TAB)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(LOG_TAB, rows=1000, cols=len(COLUMNS))
                ws.update([COLUMNS], "A1")
                ws.freeze(rows=1)
                ws.format("A1:O1", {"textFormat": {"bold": True}})
                ws.format("B:B", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
                ws.format("C:C", {"numberFormat": {"type": "TIME", "pattern": "h:mm am/pm"}})
            try:
                sh.worksheet(TOTALS_TAB)
            except gspread.WorksheetNotFound:
                t = sh.add_worksheet(TOTALS_TAB, rows=1000, cols=9)
                t.update([[TOTALS_FORMULA]], "A1", value_input_option="USER_ENTERED")
                t.freeze(rows=1)
                t.format("A1:I1", {"textFormat": {"bold": True}})
                t.format("A:A", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
            # Drop the default empty "Sheet1" if it's still there.
            try:
                default = sh.worksheet("Sheet1")
                if not any(default.get_all_values()):
                    sh.del_worksheet(default)
            except gspread.WorksheetNotFound:
                pass
            self._log = ws
            return ws

    def append(self, rows):
        # USER_ENTERED so Sheets stores real dates/times/numbers (good for reports).
        self._log_ws().append_rows(rows, value_input_option="USER_ENTERED",
                                   table_range="A1")

    def _all_rows(self):
        from gspread.utils import DateTimeOption, ValueRenderOption
        return self._log_ws().get(
            "A2:O",
            value_render_option=ValueRenderOption.unformatted,
            date_time_render_option=DateTimeOption.formatted_string,
        )

    def items_for_date(self, date):
        return [it for it in (row_to_item(r) for r in self._all_rows() if r and r[0])
                if it["date"] == date]

    def delete(self, entry_id):
        ws = self._log_ws()
        ids = ws.col_values(1)
        for idx, v in enumerate(ids, start=1):
            if idx > 1 and v == entry_id:
                ws.delete_rows(idx)
                return True
        return False


class MemoryStore:
    """Stand-in for local testing without Google credentials."""
    def __init__(self):
        self.rows = []

    def append(self, rows):
        self.rows.extend(rows)

    def items_for_date(self, date):
        return [it for it in map(row_to_item, self.rows) if it["date"] == date]

    def delete(self, entry_id):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r[0] != entry_id]
        return len(self.rows) < before


_store = None


def get_store():
    global _store
    if _store is None:
        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        sheet_id = os.environ.get("SHEET_ID")
        if raw and sheet_id:
            _store = SheetStore(sheet_id, json.loads(raw))
        elif os.environ.get("FAKE_SHEET") == "1":
            _store = MemoryStore()
        else:
            raise RuntimeError(
                "Google Sheet isn't configured. Set GOOGLE_SERVICE_ACCOUNT_JSON and "
                "SHEET_ID (or FAKE_SHEET=1 to test without Google).")
    return _store
