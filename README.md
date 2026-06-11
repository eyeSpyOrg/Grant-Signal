# Eye Spy Grant Scout

A free, local funder-research tool for the **Eye Spy Foundation** (Jacksonville, FL) —
our own version of Candid / Instrumentl, built entirely on **public IRS Form 990 data**.

- **Find Funders** — search every tax-exempt org registered with the IRS (3M+), filter by state and category.
- **Grants Database** — see *every grant* a foundation actually paid: recipient, location, purpose, and amount,
  pulled straight from their 990 filings. Vision/blindness-related grants are auto-flagged.
- **Funder profiles** — multi-year financials, qualifying distributions, key people, and links to 990 PDFs.
- **My Pipeline** — track prospects through Researching → Contacted → LOI → Applied → Awarded, with deadlines,
  contacts, and notes.
- **Accessible UI** — high contrast, large-text toggle, keyboard- and screen-reader-friendly (built for a
  blind/low-vision organization).

**No API keys, no accounts, no cost.** Data comes from the ProPublica Nonprofit Explorer API and the public
IRS 990 e-file XML release (mirrored on S3 by the GivingTuesday Data Lake). Everything you save stays on
your own computer in a local database file.

---

## Setup for the Eye Spy team (Windows — no technical experience needed)

You only do steps 1–2 once. After that, starting the app is a single double-click.

**Step 1 — Get this folder onto your computer.**
If someone sent it to you as a ZIP file: right-click the ZIP → **Extract All...** → **Extract**.
(Don't skip this — the app won't work from inside the ZIP.) Put the extracted folder
somewhere easy to find, like your Desktop or Documents.

**Step 2 — Install Python** (free, one time, ~2 minutes):
1. Go to <https://www.python.org/downloads/> and click the big yellow **Download Python** button.
2. Open the file it downloads.
3. ⚠️ **On the very first screen, tick the checkbox that says "Add python.exe to PATH"**
   (it's at the bottom and easy to miss — this is the one step people get wrong).
4. Click **Install Now** and wait for "Setup was successful".

**Step 3 — Start the app:** open the folder and **double-click `run.bat`**.
A black window appears, and a few seconds later your browser opens to the app
(<http://127.0.0.1:5000>). The first launch takes a little longer while it sets itself up.

- ✅ **Keep the black window open** while you use the app.
- 🛑 **To stop the app**, just close the black window.
- 🔁 **To use it again later**, double-click `run.bat` again. Your saved pipeline and
  grants database will still be there.

### If something goes wrong

| What you see | What it means / what to do |
|---|---|
| "Python is not installed yet" in the black window | Do Step 2. If you already did, you likely missed the **"Add python.exe to PATH"** checkbox — re-run the Python installer, choose *Modify*, or uninstall/reinstall with the box ticked. |
| Browser says "This page can't be reached" | The app is still starting. Wait 5 seconds and click the browser's refresh button. |
| "The app is ALREADY RUNNING in another window" | You started it twice. Close the new window and use the one that's already open (or just go to http://127.0.0.1:5000). |
| A Windows "protected your PC" / SmartScreen popup | Click **More info → Run anyway**. It appears because `run.bat` isn't from a software store. |
| Searches fail or pages show "are you online?" | The app needs internet to look up funders. Check your connection. Saved data still works offline. |

### Mac / Linux

```bash
cd Candid_dupe_EyeSpy
pip3 install -r requirements.txt
python3 app.py
# then open http://127.0.0.1:5000
```

### First run — load the starter data

On the Dashboard, click **"Index all starter funders now"** (or go to the **Indexer** page).
This downloads the recent 990 filings of 11 hand-picked funders — major Jacksonville-area foundations plus
national vision/blindness funders — and builds your local grants database. It takes a few minutes and only
needs to be done once. After that, index any other funder you're curious about with one click.

---

## How to use it for grant prospecting

1. **Start from peers.** In the Grants Database, search names of organizations like Eye Spy
   (e.g. "lighthouse for the blind", "vision", "braille"). Every hit shows you a foundation that has
   *already funded* this kind of work — your warmest prospects.
2. **Check fit.** Open the funder's profile: how big are their typical grants? Do they give in Florida?
   What did they fund last year? The "vision" badge flags mission-relevant grants automatically.
3. **Save it.** Click **+ Pipeline**, set a status and deadline, paste contact info into notes.
4. **Grow the database.** Use **Find Funders** to discover new foundations (try the quick searches),
   then click **Index grants** on anything promising. The more funders you index, the more powerful
   the Grants Database search becomes.

## Where the data comes from

| Data | Source | Key needed |
|---|---|---|
| Org search, profiles, financial history | [ProPublica Nonprofit Explorer API](https://projects.propublica.org/nonprofits/api) | No |
| Itemized grants, key people | IRS Form 990 e-file XML via the public [GivingTuesday 990 Data Lake](https://gt990datalake-rawdata.s3.amazonaws.com) (S3) | No |

The app fetches data on demand and caches it in `data/grantscout.db` (SQLite). Your pipeline lives in the
same file — **back it up** if your prospect list matters, and don't commit it to a shared repo if your
notes are sensitive.

## Known limitations (vs. paid tools like Candid / Instrumentl)

- **Data lag.** 990s are filed up to a year after fiscal year end and released by the IRS months later, so
  the newest grants you'll see are usually 1–2 years old. (Paid tools have the same underlying lag; they
  supplement with self-reported data.)
- **No open-RFP / deadline feed.** Application deadlines and "currently accepting applications" status are
  not in 990 data — check the funder's website (profiles link out). Candid/Instrumentl license or
  hand-curate this.
- **Donor names are not public.** Schedule B (who donated *to* an org) is redacted by law for everyone,
  including Candid. You can see who a foundation *gives to*, not who gives to a public charity.
- **Paper filers.** A small number of older/smaller foundations filed on paper; their grants aren't in the
  XML release (the profile still links the scanned PDF).
- **Grants under $5,000** from public charities (Schedule I) don't have to be itemized. Private foundation
  (990-PF) grant lists are complete.
- **Be polite.** ProPublica's API is free with no hard published quota, but it's shared infrastructure —
  the app caches aggressively and paces its requests. Don't try to index thousands of funders in one sitting.

## Files

```
app.py            Flask web app (routes/pages)
db.py             SQLite storage (caches, grants, pipeline)
propublica.py     ProPublica API client
xml990.py         IRS 990 XML download + grant/people parser
indexer.py        Background indexing worker
seed_funders.py   Curated starter funder list (verified EINs)
templates/        HTML pages    static/  CSS + JS
data/             Local database (created on first run)
run.bat           One-click Windows launcher
```
