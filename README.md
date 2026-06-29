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

**No API keys, no cost.** Data comes from the ProPublica Nonprofit Explorer API and the public
IRS 990 e-file XML release (mirrored on S3 by the GivingTuesday Data Lake).

---

## Use the website (no setup needed)

The app is hosted and shared by the whole team at:

**👉 <https://eyespy-grant-scout.onrender.com>**

Just open that link, click **Register** (top right) to create your account, and log in. Your personal
pipeline is private to you; the Team Pipeline and Grants Database are shared with everyone on the team.

> Note: the site is on Render's free tier, so it may take ~30–50 seconds to wake up if no one has visited
> recently — that's normal, just wait for the first page to load.

---

## Running it locally (for development)

This app now stores its data in Postgres, not a local file, so running it locally requires a
`DATABASE_URL` pointing at a Postgres database (a local Postgres install, or the connection string
from the Render dashboard). `run.bat` and `python app.py` will fail immediately without it.

**Step 1 — Get this folder onto your computer** (clone the repo, or extract the ZIP).

**Step 2 — Install Python 3.11+** from <https://www.python.org/downloads/> (on Windows, tick
**"Add python.exe to PATH"** during install).

**Step 3 — Set `DATABASE_URL`** to a Postgres connection string, e.g.:

```bash
export DATABASE_URL="postgresql://user:password@localhost:5432/eyespy_dev"
```

(On Windows: `set DATABASE_URL=postgresql://...` before running `run.bat`, or add it as a
system environment variable.)

**Step 4 — Install dependencies and start the app:**

```bash
pip install -r requirements.txt
python app.py
# then open http://127.0.0.1:5001
```

On Windows you can still double-click `run.bat` once `DATABASE_URL` is set in your environment.

### If something goes wrong

| What you see | What it means / what to do |
|---|---|
| `RuntimeError: DATABASE_URL is not set` | Set the `DATABASE_URL` environment variable (Step 3) before starting the app. |
| Browser says "This page can't be reached" | The app is still starting. Wait 5 seconds and refresh. |
| "The app is ALREADY RUNNING in another window" | You started it twice. Close the new window and use the one already open. |
| Searches fail or pages show "are you online?" | The app needs internet to look up funders. Check your connection. |

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

The app fetches data on demand and caches it in Postgres. The Grants Database and Team Pipeline are
shared across everyone who logs in; each person's "My Pipeline" is private to their account.

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
auth.py           Login/session/API-token decorators
db.py             Postgres storage (caches, grants, pipeline)
propublica.py     ProPublica API client
xml990.py         IRS 990 XML download + grant/people parser
indexer.py        Background indexing worker
seed_funders.py   Curated starter funder list (verified EINs)
templates/        HTML pages    static/  CSS + JS
render.yaml       Render deploy config (web service + Postgres)
run.bat           One-click Windows launcher (needs DATABASE_URL set)
```
