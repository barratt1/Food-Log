# Food Log: Voice Food Tracker

Tap a button, say what you ate, and the app lists each food with its
calories, protein, carbs, fat, fiber, sugar and sodium. You check the list,
tap **Save**, and each food becomes a row in your Google Sheet, stamped with
the date on your phone. The **Daily** tab shows everything you've logged for a
day, with totals, and lets you go back to earlier days.

This is a separate app from Bird Dog Quote. It has its own folder, GitHub
repo, Render service and OpenAI key, and shares nothing with Bird Dog.

## How it works

1. **Phone page** (`static/index.html`): a **Log food** tab with the record
   button and a **Daily** tab for viewing any day.
2. **Transcription**: the recording goes to OpenAI Whisper (about
   $0.006/minute).
3. **Nutrition**: GPT-4o-mini splits what you said into separate foods and
   estimates nutrition for each portion (a fraction of a cent per entry).
4. **Review**: edit any food, portion or number, remove items, or add one
   before saving. You can also change the date to log something you forgot
   yesterday.
5. **Save**: each food is added as a row to the **Log** tab of your Google
   Sheet, using the phone's local date and time. That way every entry lands
   on the right day, even though Render's servers run on UTC time.
6. **Daily view**: reads the Log tab for the chosen date and totals it.

### The Google Sheet

The app creates two tabs the first time it runs:

| Tab | What's in it |
|---|---|
| **Log** | One row per food: Entry ID, Date, Time, Meal, Food, Portion, Calories, Protein, Carbs, Fat, Fiber, Sugar, Sodium, Spoken Text, Logged At |
| **Daily Totals** | A formula that rolls the Log up into one row per day, updating automatically |

Everything lives in one Log tab instead of a tab per day. That keeps the sheet
well under Google's 10-million-cell limit and makes weekly and monthly
reports easy: add a pivot table on the Log tab, or connect the sheet to
Looker Studio (free) for charts.

Don't rename the tabs or reorder the Log columns. You can add your own tabs
(e.g. a Weekly summary) freely.

---

## Setup (about 30 minutes, one time)

### 1. Create the Google Sheet
1. Go to https://sheets.new and name it something like **Food Log**.
2. Copy the **Sheet ID** from the URL, the long part between `/d/` and
   `/edit`:
   `https://docs.google.com/spreadsheets/d/`**`1AbC...xyz`**`/edit`

### 2. Create a Google service account (free)
This is a "robot" Google user the app signs in as. No Zapier or other paid
service is needed.

1. Go to https://console.cloud.google.com and create a new project, e.g.
   **food-log**.
2. **APIs & Services → Library**: search **Google Sheets API** and click
   **Enable**.
3. **APIs & Services → Credentials → Create credentials → Service account**.
   Name it `food-log-bot`, click **Done** (it doesn't need any roles).
4. Click the new service account → **Keys** tab → **Add key → Create new
   key → JSON**. A `.json` file downloads. **Keep it private**, because it's
   a password. Never put it in GitHub.
5. Copy the service account's email (it looks like
   `food-log-bot@food-log-12345.iam.gserviceaccount.com`).
6. Open your Google Sheet → **Share** → paste that email → **Editor** →
   uncheck "Notify" → **Share**.

> If Google says "Service account key creation is disabled", your Google
> account is under an organization policy. Use a personal Gmail account for
> this project instead.

### 3. Create a new OpenAI key
1. https://platform.openai.com/api-keys → **Create new secret key** → name
   it `food-log`.
2. Use a **new** key rather than the Bird Dog one, so you can see each
   app's usage separately and turn one off without affecting the other.

### 4. (Optional) Try it on your computer first
```bash
cd food-tracker-app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...your-food-log-key...
export FAKE_SHEET=1          # test without Google: data is kept in memory only
python app.py
```
Open http://localhost:5000. Use **"or type it instead"**, because browsers
only allow the mic on `localhost` or HTTPS.

To test with the real sheet, drop `FAKE_SHEET` and set:
```bash
export SHEET_ID=1AbC...xyz
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat ~/Downloads/food-log-12345-abc.json)"
```

### 5. Put it on GitHub
Create a **new, private** repo (e.g. `food-log-app`) and push this folder to
it. The `.gitignore` keeps `.env` and key files out, but double-check
that no `.json` key file is in the folder before pushing.

### 6. Deploy on Render
1. Render dashboard → **New → Web Service** → connect the new
   `food-log-app` repo. (This is a new service; your Bird Dog service is
   untouched.)
2. Settings:
   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app --timeout 120`
3. **Environment** → add:

   | Key | Value |
   |---|---|
   | `OPENAI_API_KEY` | your new food-log key |
   | `SHEET_ID` | the ID from step 1 |
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | open the downloaded `.json` file in a text editor, copy **everything**, paste it here |
   | `APP_PASSCODE` | any code you like, e.g. `4821`. Recommended, so a stranger who finds the URL can't log food or spend your OpenAI credit |

4. **Create Web Service**. When it's live, open the `onrender.com` URL. The
   first save creates the Log and Daily Totals tabs in your sheet.

**Free vs. paid:** Render's free plan sleeps after ~15 minutes idle and
takes 30–60 seconds to wake. For an app you open several times a day, the
**Starter** plan (~$7/month) keeps it instant.

### 7. Use your own domain
1. In the Render service → **Settings → Custom Domains → Add**, e.g.
   `food.yourdomain.com`.
2. Render shows a **CNAME** record. Add it at your domain registrar's DNS
   settings (name `food`, value `your-service.onrender.com`).
3. Wait for Render to show **Verified** and issue the HTTPS certificate
   (minutes to an hour).

### 8. Put it on your phone
Open your URL in Safari → **Share → Add to Home Screen**. (Android: Chrome
menu ⋮ → **Add to Home screen**.) Enter your passcode once.

---

## Everyday use
- Talk naturally, any time of day: *"For lunch I had a Chipotle chicken bowl
  with rice, black beans and guac, and a Diet Coke."*
- Give amounts when you can ("6 ounces of salmon", "a cup of rice"). The
  estimates are only as good as the portions.
- Always glance at the review screen before saving. Calories are AI
  estimates from typical USDA and published restaurant values, not lab
  measurements.
- Forgot something yesterday? Change **Log for** on the review screen
  before saving.
- Delete a mistake on the **Daily** tab: tap **×**, then **Delete**.

## Adjusting the nutrition step
The instructions the AI follows are plain English in `app.py` under
`NUTRITION_PROMPT`. Edit them to change behavior, e.g. "always assume
2% milk". To try a newer or stronger model, set an `OPENAI_MODEL` environment
variable on Render (default `gpt-4o-mini`).

## Adding more people
Everyone uses the **same app and URL**. Each person gets their own **code**,
and that code decides which Google Sheet their food goes into. People only
ever see their own data.

1. Create a Google Sheet for each person, and **share each one (Editor) with
   the same service-account email** (`food-log-bot@...`).
2. In Render → **Environment**, add a variable named `USERS` with one entry
   per person (including you):
   ```json
   [
     {"name": "Christian", "code": "4821", "sheet": "1AbC...your sheet id"},
     {"name": "Sam",       "code": "7733", "sheet": "1XyZ...Sam's sheet id"},
     {"name": "Pat",       "code": "5190", "sheet": "1QwE...Pat's sheet id"}
   ]
   ```
   Every code must be different. Use 4+ digits, since the code is the
   person's login.
3. Delete `APP_PASSCODE` and `SHEET_ID` (`USERS` replaces them), then
   **Save and deploy**.
4. Send each person the URL and their code. They enter it once, and the app
   shows their name at the top. **Not you?** switches to a different
   person's code on a shared phone.

To add someone later, add a line to `USERS` and save. To remove someone,
delete their line. Their sheet stays as it is.

All OpenAI usage is billed to your key. Each entry costs a fraction of a
cent, so a few people add very little.

## Files
- `app.py`: Flask server (transcribe, nutrition lookup, save, daily view, delete)
- `sheets.py`: Google Sheets storage and the auto-created tabs
- `static/index.html`: the phone app (Log food + Daily tabs)
- `static/manifest.json`, `static/icon-*.png`: home-screen icon
- `requirements.txt`: Python dependencies
