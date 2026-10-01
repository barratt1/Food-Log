"""
Voice Food Tracker
Flask backend: receives a voice recording (or typed text), transcribes it,
has the AI list each food with estimated nutrition, and saves the reviewed
items to a Google Sheet, one row per food, stamped with the phone's date.

Env vars:
    OPENAI_API_KEY               your OpenAI API key (use a separate key from Bird Dog)
    GOOGLE_SERVICE_ACCOUNT_JSON  full contents of the Google service-account key file
    SHEET_ID                     ID of the Google Sheet (from its URL)
    APP_PASSCODE                 optional: a code the app asks for once per phone
    OPENAI_MODEL                 optional, default gpt-4o-mini
    TRANSCRIBE_MODEL             optional, default whisper-1

Run locally:
    pip install -r requirements.txt
    export OPENAI_API_KEY=sk-...
    export FAKE_SHEET=1          # try it without Google first
    python app.py
    open http://localhost:5000
"""
import datetime
import json
import os
import tempfile
import uuid
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory
from openai import OpenAI

from sheets import NUTRIENT_KEYS, get_store, item_to_row

app = Flask(__name__, static_folder="static")
_client = None


def get_client():
    global _client
    if _client is None:
        _client = OpenAI()  # reads OPENAI_API_KEY from env
    return _client


NUTRITION_PROMPT = """You are a nutrition logging assistant. The user spoke (or \
typed) what they just ate. Split it into separate food items and estimate the \
nutrition for each one AS EATEN, using typical USDA values for the stated \
portion.

Rules:
- One item per distinct food. "Two eggs and toast with butter" is three items: \
eggs (2 large), toast (1 slice), butter (1 tsp). Combine only when it's one \
dish (e.g. "a turkey sandwich" is one item unless they list the parts).
- portion: what they said, or a sensible default if they didn't say \
(e.g. "1 medium", "1 cup", "1 slice"). Put the quantity in the portion, not \
in the food name.
- Brand or restaurant names (e.g. "Chick-fil-A sandwich", "Chobani yogurt"): \
use that product's published nutrition if you know it.
- meal: Breakfast, Lunch, Dinner, or Snack. Use what they said; otherwise \
infer from the local time given.
- Numbers only (no units). calories, protein_g, carbs_g, fat_g, fiber_g, \
sugar_g as whole numbers or one decimal; sodium_mg as a whole number.
- Ignore filler words and anything that isn't food or drink. Water and black \
coffee are fine to include with 0 calories.
- If nothing they said is food, return an empty list.

Respond with ONLY a JSON object:
{"items": [{"food": "", "portion": "", "meal": "", "calories": 0, \
"protein_g": 0, "carbs_g": 0, "fat_g": 0, "fiber_g": 0, "sugar_g": 0, \
"sodium_mg": 0}]}"""


def require_passcode(fn):
    """If APP_PASSCODE is set, API calls must send it in X-App-Passcode."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        code = os.environ.get("APP_PASSCODE")
        if code and request.headers.get("X-App-Passcode") != code:
            return jsonify({"error": "passcode", "message": "Wrong or missing passcode."}), 401
        return fn(*args, **kwargs)
    return wrapper


def error(msg, status=400):
    return jsonify({"error": "bad_request", "message": msg}), status


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/ping")
@require_passcode
def ping():
    return jsonify({"ok": True})


@app.route("/api/analyze", methods=["POST"])
@require_passcode
def analyze():
    """Audio (multipart 'audio') or text (form/JSON 'text') -> food items."""
    local_time = request.values.get("local_time", "")
    text = (request.values.get("text") or "").strip()
    if not text and request.is_json:
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        local_time = body.get("local_time", local_time)

    if not text:
        if "audio" not in request.files:
            return error("Send a recording or some text.")
        audio = request.files["audio"]
        # Keep the real extension: iPhones record mp4/m4a, Android/Chrome webm.
        ext = os.path.splitext(audio.filename or "")[1] or ".webm"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            audio.save(tmp.name)
            tmp_path = tmp.name
        try:
            with open(tmp_path, "rb") as f:
                result = get_client().audio.transcriptions.create(
                    model=os.environ.get("TRANSCRIBE_MODEL", "whisper-1"),
                    file=f,
                    prompt="A list of foods and drinks someone ate, with amounts.",
                )
            text = (result.text or "").strip()
        finally:
            os.unlink(tmp_path)
        if not text:
            return error("I couldn't hear anything in that recording. Try again?")

    completion = get_client().chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": NUTRITION_PROMPT},
            {"role": "user", "content": f"Local time: {local_time or 'unknown'}\n\n{text}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    try:
        items = json.loads(completion.choices[0].message.content).get("items", [])
    except (ValueError, AttributeError):
        items = []
    return jsonify({"transcript": text, "items": [_clean_item(i) for i in items]})


def _clean_item(item):
    out = {
        "food": str(item.get("food", "")).strip(),
        "portion": str(item.get("portion", "")).strip(),
        "meal": str(item.get("meal", "")).strip(),
    }
    for k in NUTRIENT_KEYS:
        try:
            n = float(item.get(k) or 0)
        except (TypeError, ValueError):
            n = 0
        out[k] = int(n) if n.is_integer() else round(n, 1)
    return out


def _valid_date(s):
    try:
        datetime.date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


@app.route("/api/log", methods=["POST"])
@require_passcode
def log_items():
    """Save reviewed items. The phone sends its own local date and time so that
    entries land on the right day no matter what timezone the server is in."""
    body = request.get_json(silent=True) or {}
    date = body.get("date", "")
    time = body.get("time", "")
    items = [_clean_item(i) for i in body.get("items", []) if str(i.get("food", "")).strip()]
    if not _valid_date(date):
        return error("Missing or invalid date (expected yyyy-mm-dd).")
    if not items:
        return error("Nothing to save.")

    logged_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    transcript = str(body.get("transcript", ""))[:2000]
    rows = []
    for item in items:
        # Starts with a letter so Sheets never reads it as a number.
        entry_id = "e" + uuid.uuid4().hex[:10]
        item["id"] = entry_id
        rows.append(item_to_row(entry_id, date, time, item, transcript, logged_at))
    get_store().append(rows)
    return jsonify({"saved": len(rows), "items": items})


@app.route("/api/day")
@require_passcode
def day():
    date = request.args.get("date", "")
    if not _valid_date(date):
        return error("Missing or invalid date (expected yyyy-mm-dd).")
    items = get_store().items_for_date(date)
    totals = {k: 0 for k in NUTRIENT_KEYS}
    for it in items:
        for k in NUTRIENT_KEYS:
            totals[k] += it[k]
    totals = {k: round(v, 1) for k, v in totals.items()}
    return jsonify({"date": date, "items": items, "totals": totals})


@app.route("/api/entry/<entry_id>", methods=["DELETE"])
@require_passcode
def delete_entry(entry_id):
    if not get_store().delete(entry_id):
        return error("That entry wasn't found.", 404)
    return jsonify({"deleted": entry_id})


@app.errorhandler(Exception)
def handle_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"error": "http", "message": e.description}), e.code
    app.logger.exception("Unhandled error")
    return jsonify({"error": "server", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
