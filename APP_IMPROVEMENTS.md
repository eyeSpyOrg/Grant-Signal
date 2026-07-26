# Grant Signal — Improvement Backlog

Findings from a real research session (2026-07-26): using the deployed app at
`eyespy-grant-scout.onrender.com` as a **normal logged-out user** to find 10 new
funders for the Eye Spy HubSpot tracker. Listed roughly by how much each one cost
me during the actual task.

**Status: all 19 items implemented (2026-07-26).** See the notes at the bottom for the
two that were deliberately scoped. Verified end-to-end against live ProPublica and
Grants.gov data, plus a 92-check regression suite.

---

## P0 — Blocks the core job

### 1. Find Funders returns charities, not grantmakers
Searching `blind` + `FL` returns 73 results that are almost entirely *service
organizations seeking money*, not organizations giving it. Same for `sight
foundation` and `visually impaired`. There is no way to filter to grantmakers.

The data is already there — `org_profile()` computes `is_foundation` from the
presence of a `990-PF` filing. It just isn't available as a search filter.

**Fix:** a "Grantmakers only (990-PF)" checkbox on `/search`.
This is the single biggest time sink in the tool.

### 2. 990-PF status is invisible until you click into each org
Even without a filter, triage would be possible if results were badged inline.
Today you must open every result to learn whether it's a funder. I opened ~10 org
pages to qualify 3 funders.

**Fix:** render a `Private foundation — makes grants` badge on each search row.

### 3. Grants Database is empty in production
Dashboard reports `0 grants ($0) from 0 indexed funders`. `/grants` has presets
("Vision & blindness grants", "Jacksonville recipients", "Disability") that all
return nothing. The feature that answers *"which foundations actually fund BVI
orgs"* — the app's best idea — is dead on arrival for every new user.

**Fix:** ship a pre-indexed database with the deploy, or run seed indexing as a
startup/background job. A first-time user should never see a zero state here.

### 4. Federal opportunity APIs are unreachable
`/api/opportunities` (grants.gov) and `/api/nih-projects` (NIH RePORTER) exist in
`app.py` but:
- have **no page and no nav entry** — a user cannot reach them at all
- return `{"error":"API token required"}` when hit directly

Both wrap *public, read-only, unauthenticated* government data. Gating them behind
a token buys nothing and cost this session the entire federal half of the research.
The tracker currently has **zero** federal opportunities logged, so this is also
the biggest untapped funding channel.

**Fix:** build an `/opportunities` page in the nav; drop auth on read-only public
data (or make these endpoints session-optional like `/search` and `/grants`).

*Update (federal session):* confirmed still blocked — `/api/token` returns
`{"error":"Unauthorized"}` logged out. The federal research below had to be done by
importing `grants_gov.py` and calling it from a script, i.e. the app's own client
code works fine and only the HTTP layer is in the way.

---

## P1 — Causes wasted research

### 5. `Qualifying distributions` renders `$0` for funders that obviously grant
| Funder | Total expenses (2023) | Qualifying distributions shown |
|---|---|---|
| Lavelle Fund for the Blind | $7,030,730 | **$0** |
| May & Stanley Smith Charitable Trust | $28,173,568 | **$0** |
| Florida Lions Foundation for the Blind | $562,155 | $1,562,093 ✓ |

Two of the largest BVI/disability funders in the country look inactive. Florida
Lions renders correctly, so the field is being read inconsistently from the
ProPublica payload rather than being universally absent.

**Fix:** verify the field mapping across `990-PF` variants; fall back to `—`
(unknown) instead of `$0`, which reads as a factual "this funder gives nothing."

### 6. No geographic-eligibility signal anywhere
Geography is the #1 disqualifier in this space. During this session I killed three
otherwise-perfect candidates entirely by hand:

- **Kessler Foundation** — restricted to AL/AR/KY/MS/WV, FL ineligible
- **Ability Central** — California only
- **Coleman Foundation** — Cook County, IL only

The app showed none of this. Funder state is known; stated *service-area
restrictions* are not captured at all.

**Fix:** at minimum surface funder state prominently and flag out-of-state
grantmakers; ideally add a free-text "eligibility notes" field on the pipeline
record so a disqualification gets recorded once, not rediscovered by each teammate.

### 7. Dedupe only knows about the in-app pipeline
The `In pipeline` badge genuinely worked — it's how Mitsubishi Electric America
Foundation got caught as an existing duplicate before I wasted time on it. But it
only covers records entered in the app. The team's real source of truth is the
HubSpot sheet with ~100 deals across 9 tabs, and the app is blind to all of it.

**Fix:** a CSV import that mirrors the existing HubSpot export, so every search
result can be checked against what the team has already researched. Highest
leverage item on this list for a multi-person team.

### 8. Search is literal name-matching, not semantic
`foundation for the blind` matches those exact words in an org name. It cannot
answer the question a grant researcher actually has: *"who funds assistive
technology for BVI adults?"* NTEE and 990-PF status also can't be combined.

**Fix:** at minimum allow NTEE + grantmaker-only + state together. Longer term,
semantic search over 990 grant purpose text (once #3 is fixed) is the real unlock.

---

## P2 — Polish

### 9. `/find-funders` 404s
The nav label reads "Find Funders" but the route is `/search`. Guessing the URL
gives a bare unstyled Flask 404 with no nav and no way back.
**Fix:** alias the route, ship a branded 404.

### 10. ~25s cold start with no warning
Render free tier. First impression for a funder or board member clicking the link
is a black terminal screen with ASCII art. **Fix:** keep-warm ping, or a branded
loading page.

### 11. Indexing is manual, one funder at a time, ~30s each
Researching 20 candidates means 20 clicks and 20 waits, with no queue visibility
from the org page. **Fix:** multi-select indexing from search results + a progress
indicator that doesn't require the Indexer page.

### 12. No fit scoring on funder search
`is_vision_match()` already exists and is applied to grants and the dashboard, but
there's no equivalent "why this matched" signal on funder results.

### 13. Research → sheet round trip is fully manual
There's a HubSpot CSV export for the pipeline, but getting a funder from search
into the team's Google Sheet still meant hand-typing 15 rows, 7 columns each.
**Fix:** "copy as HubSpot row" / "add to pipeline with notes" straight from the
org page, so the research and the CRM row are never retyped.

---

## Round 2 — from the federal research session (`grants_gov.py`)

Same day, second pass: using `grants_gov.py` to find federal opportunities.
These are specific to the grants.gov integration.

### 14. grants.gov keyword search is unusable for this domain (P0)
Searching `blind` against grants.gov returns 13 results, of which **zero** are
about blindness:

```
[posted] Addressing Methodological Challenges with Clinical Trials ... (double-blind)
[posted] DoW, Ovarian Cancer, Investigator-Initiated Research Award
[posted] F27AS00008-NAWCA 2027-1 US Standard Grants          (waterfowl conservation)
[posted] Multi Modal Materials Analysis (MMoMA)              (DARPA)
```

`vision loss` (341 hits) surfaces `DoW Lupus, Transformative Vision Award`.
`assistive technology` (748 hits) surfaces semiconductor and quantum-computing
NOFOs. The keyword is matched against full opportunity text, so common English
words in unrelated abstracts dominate.

**This exact problem is already solved elsewhere in the codebase.** `nih_reporter.py`
carries a comment explaining that searching NIH abstracts turns `blindness` into
20,000+ hits from "double-blind trial", and deliberately restricts to
`search_field: "projecttitle"`. That lesson was never carried over to
`grants_gov.py`.

**Fix:** title-scoped matching and/or a curated BVI query set; treat raw keyword
search as the fallback, not the default.

### 15. Agency filtering works and isn't exposed (P0)
`search(agencies=["HHS-ACL"])` returns 27 results that are almost *all* relevant —
NIDILRR, Centers for Independent Living, ABLE accounts. `agencies=["ED"]` returns 2,
both OSERS/OSEP. This is the query shape that actually finds federal disability
money, and `grants_gov.search()` already supports it via the `agencies` parameter.
Nothing in the app surfaces it.

**Fix:** ship an agency picker pre-loaded with the agencies that matter for this
mission (HHS-ACL, ED, HHS-NIH11/NEI, NSF, IMLS), and default to browsing by agency
rather than keyword.

### 16. No `fetchOpportunity` — the go/no-go fields are missing (P1)
`grants_gov.py` has `search()` but no detail fetch. Search results carry title,
dates and agency, but **not** award ceiling/floor, estimated funding, or applicant
eligibility — which are the only fields that determine whether a grant is worth
pursuing. I had to call `api.grants.gov/v1/api/fetchOpportunity` directly.

This mattered concretely: it's how I learned NIDILRR DRRP awards are $495K–$500K
and that eligibility explicitly includes *"public or private organizations"* —
correcting an assumption I'd already written into the tracker that these were
effectively universities-only. A researcher without the detail fetch would have
wrongly skipped the entire program.

**Fix:** add `fetch(opportunity_id)` to `grants_gov.py`; show ceiling/floor and
eligibility on the opportunity row.

### 17. Truncated titles cause real, silent errors (P1)
The listing showed:

```
HHS-2026-ACL-NIDILRR-DPEM-0223 ... Employment Among People Who Are …
```

I read that as *blind or low vision* — a reasonable guess for a BVI tool, and the
strongest-looking federal match of the whole session. It is actually **Deaf or Hard
of Hearing**. Only the detail fetch caught it. Same trap on `ED-GRANTS-060126-001`.

**Fix:** never truncate the disability population out of a title; show full titles
on hover/expand at minimum.

### 18. No archived / recurrence search (P1)
The most valuable federal finding of the session was a *negative* one: **every
BVI-specific federal program is currently closed**, and they run on ~5-year cycles.

| Program | Rounds |
|---|---|
| RRTC, Employment of People Who Are Blind or Have Low Vision | 2015, 2020, 2025 |
| RERC on Blindness and Low Vision | 2016, 2021, 2022 |
| NEI, Low Vision & Blindness Accessibility Tools | 2024 |
| ED/RSA Rehabilitation Training, Blind/VI | 2009–2025, ~annual |

That pattern is only visible via `opp_statuses="closed|archived"`. The API supports
it, `grants_gov.py` supports it via the `opp_statuses` parameter, and the app never
exposes it. For a small nonprofit, knowing *when the next window opens* is worth
more than any single currently-open RFP.

**Fix:** an "include past opportunities" toggle, and group results by program
lineage so the cadence is visible at a glance.

### 19. No deadline watching for opportunities (P2)
`/deadlines` and the dashboard calendar track *pipeline* items the user typed in.
Nothing watches grants.gov for a reissue of a program you care about. Given #18,
a saved-search alert ("tell me when NEI reissues an accessibility-tools RFA") is
the highest-value federal feature this tool could have.

---

## Implementation notes (2026-07-26)

Two items were delivered in a narrower form than written, because the form written
isn't achievable in this stack:

- **#10 (cold start).** A keep-warm ping cannot come from the app itself — Render
  only resets its idle timer on *inbound* traffic, and a branded loading page can't
  be served by a process that is asleep. Delivered instead: a cheap `/healthz`
  endpoint plus README instructions for pointing a free external uptime pinger at
  it, which is what actually eliminates the cold start.
- **#19 (alerts).** The app has no email or push infrastructure, so "tell me when"
  is delivered as a **Watchlist** page: saved grants.gov searches are re-run on each
  visit and anything posted since your last visit is flagged `new`. Catching a
  reissue still requires opening the page rather than receiving a message.

Three bugs surfaced while testing the above and were fixed alongside them:

- Schema DDL ran on *every* new database connection, and `ALTER TABLE` takes an
  exclusive lock even when the column already exists — enough to deadlock under
  ordinary concurrent traffic. It now runs once per process behind a Postgres
  advisory lock.
- Connections were never committed after reads, so each one sat `idle in
  transaction` holding locks indefinitely, blocking any later schema change. A
  Flask teardown handler now closes out each request's transaction.
- The pipeline edit form silently dropped any field it didn't post, so the seed
  bootstrap and the two pipeline pages could blank each other's data.

