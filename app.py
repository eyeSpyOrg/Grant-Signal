"""Eye Spy Grant Scout — a free, local funder-research tool built on public IRS 990 data.

Run:  python app.py   then open http://127.0.0.1:5000
Data: ProPublica Nonprofit Explorer API (no key) + IRS 990 e-file XML via the
      public GivingTuesday data lake on S3 (no key).
"""
import csv
import datetime
import io
import os
import time

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, Response
from flask import session, redirect
import auth
import db
import grants_gov
import indexer
import nih_reporter
import propublica
import seed_funders
from seed_funders import EYESPY_EIN, EYESPY_NAME, HOME_STATE, SEED_FUNDERS, is_vision_match

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "eyespy-grant-scout-dev")
# Release each request's database transaction; a connection left open holds locks
# that block schema changes and other workers.
app.teardown_appcontext(db.end_request)

@app.template_filter("money")
def money(v):
    if v is None:
        return "—"
    try:
        return "${:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return str(v)


@app.template_filter("ein_fmt")
def ein_fmt(ein):
    s = str(ein).replace("-", "").zfill(9)
    return f"{s[:2]}-{s[2:]}"


@app.context_processor
def inject_globals():
    return {"vision_match": is_vision_match}


# ---------------- Dashboard ----------------

@app.route("/")
def dashboard():
    import datetime
    import calendar as cal_module
    
    stats = db.grants_stats()
    team_prospects = [dict(p) for p in db.pipeline_all_team()[:6]]
    for p in team_prospects:
        p["created_by_username"] = db.get_username_by_id(p["created_by_user_id"]) if p["created_by_user_id"] else "Unknown"
    
    # Mini calendar
    today = datetime.date.today()
    year = today.year
    month = today.month
    deadlines = db.deadlines_by_month(session.get("user_id"), year, month) if session.get("user_id") else []
    deadline_map = {d["deadline"][:10]: True for d in deadlines}
    calendar_grid = cal_module.monthcalendar(year, month)
    
    # Vision grants
    vision_grants = [g for g in db.search_grants(limit=5000)
                     if is_vision_match(g["purpose"], g["recipient_name"])]

    # Grants-by-year bar chart (pure CSS bars, scaled relative to the busiest year)
    by_year = db.grants_by_year()
    max_year_n = max((y["n"] for y in by_year), default=0)
    for y in by_year:
        y["pct"] = round(100 * y["n"] / max_year_n) if max_year_n else 0

    avg_grant = (stats["total"] / stats["grants"]) if stats["grants"] else 0

    # Pipeline funnel summary (Active Drafts / Requested / Submitted / In Pipeline / Next Deadline)
    funnel = db.pipeline_funnel_stats()
    if funnel["next_deadline"]:
        try:
            d = datetime.datetime.strptime(funnel["next_deadline"]["deadline"][:10], "%Y-%m-%d").date()
            funnel["next_deadline"]["deadline_fmt"] = "{} {}, {}".format(d.strftime("%b"), d.day, d.year)
        except ValueError:
            funnel["next_deadline"]["deadline_fmt"] = funnel["next_deadline"]["deadline"]

    eyespy = None
    try:
        data = propublica.get_org(EYESPY_EIN)
        if data:
            eyespy = {"org": data["organization"],
                      "history": propublica.financial_history(data)[:3]}
    except Exception:
        pass
    
    return render_template("dashboard.html", 
                           stats=stats, 
                           team_prospects=team_prospects,
                           month_name=cal_module.month_name[month],
                           year=year, month=month,
                           cal=calendar_grid, 
                           deadline_map=deadline_map,
                           prev_year=year-1 if month==1 else year,
                           prev_month=(month-2)%12+1 if month==1 else month-1,
                           next_year=year+1 if month==12 else year,
                           next_month=(month%12)+1 if month==12 else month+1,
                           vision_grants=vision_grants[:10],
                           vision_total=len(vision_grants),
                           by_year=by_year,
                           avg_grant=avg_grant,
                           funnel=funnel,
                           eyespy=eyespy,
                           seed=SEED_FUNDERS,
                           statuses=db.PIPELINE_STATUSES)

# ---------------- Funder search (ProPublica) ----------------

# When "grantmakers only" is on we may have to look past the first page of results
# to fill a screen, but each extra page costs a round of API lookups — so the scan
# is hard-capped.
MAX_PF_SCAN_PAGES = 3
PF_TARGET_RESULTS = 20


def _annotate_orgs(orgs, kinds, pipeline_eins):
    """Attach the triage signals a grant researcher needs *before* opening a profile:
    is it a grantmaker, is it in our state, does it look vision-related, is it a dupe."""
    out = []
    for o in orgs:
        o = dict(o)
        ein = str(o.get("ein"))
        kind = kinds.get(ein) or {}
        state = (o.get("state") or kind.get("state") or "").upper()
        o["is_foundation"] = kind.get("is_foundation")
        o["latest_form"] = kind.get("latest_form")
        o["grants_paid"] = kind.get("grants_paid")
        o["classified"] = ein in kinds
        o["in_pipeline"] = ein in pipeline_eins
        o["out_of_state"] = bool(state) and state != HOME_STATE

        signals = []
        if kind.get("is_foundation"):
            signals.append("Private foundation (990-PF) — makes grants")
        if is_vision_match(o.get("name"), o.get("sub_name")):
            signals.append("Vision/blindness keyword in name")
        if state == HOME_STATE:
            signals.append(f"Based in {HOME_STATE} — same state as Eye Spy")
        if kind.get("grants_paid"):
            signals.append("Reports grants paid on its latest 990-PF")
        o["signals"] = signals
        out.append(o)
    return out


@app.route("/search")
@app.route("/find-funders")   # the nav label says "Find Funders"; don't 404 the guess
def search():
    q = request.args.get("q", "").strip()
    state = request.args.get("state", "")
    ntee = request.args.get("ntee", "")
    pf_only = request.args.get("pf_only") == "1"
    page_raw = request.args.get("page", "0")
    page = int(page_raw) if page_raw.isdigit() else 0
    results = None
    error = None
    orgs = []
    scanned_pages = 1
    if q or state or ntee:
        try:
            ntee_id = int(ntee) if ntee.isdigit() else None
            results = propublica.search(q=q, state=state or None, ntee=ntee_id, page=page)
            raw = list(results.get("organizations") or [])
            pipeline_eins = db.pipeline_known_eins()
            kinds = propublica.classify_orgs([o["ein"] for o in raw])
            orgs = _annotate_orgs(raw, kinds, pipeline_eins)

            if pf_only:
                orgs = [o for o in orgs if o["is_foundation"]]
                # Grantmakers are a small slice of any result set, so read ahead a
                # couple of pages rather than showing three cards and a Next button.
                next_page = page + 1
                while (len(orgs) < PF_TARGET_RESULTS
                       and scanned_pages < MAX_PF_SCAN_PAGES
                       and next_page < (results.get("num_pages") or 0)):
                    more = propublica.search(q=q, state=state or None, ntee=ntee_id, page=next_page)
                    more_raw = list(more.get("organizations") or [])
                    if not more_raw:
                        break
                    more_kinds = propublica.classify_orgs([o["ein"] for o in more_raw])
                    orgs += [o for o in _annotate_orgs(more_raw, more_kinds, pipeline_eins)
                             if o["is_foundation"]]
                    next_page += 1
                    scanned_pages += 1
        except Exception as e:
            error = f"Search failed (are you online?): {e}"
    return render_template("search.html", q=q, state=state, ntee=ntee, page=page,
                           pf_only=pf_only, orgs=orgs, scanned_pages=scanned_pages,
                           results=results, error=error, home_state=HOME_STATE,
                           states=propublica.US_STATES, ntee_categories=propublica.NTEE_CATEGORIES,
                           indexer_status=indexer.status())


# ---------------- Organization profile ----------------

@app.route("/org/<ein>")
def org_profile(ein):
    ein = str(ein).replace("-", "")
    error = None
    data = None
    try:
        data = propublica.get_org(ein)
    except Exception as e:
        error = f"Could not load organization (are you online?): {e}"
    if data is None and error is None:
        error = "Organization not found."
    org = data["organization"] if data else None
    history = propublica.financial_history(data) if data else []
    is_foundation = any(h["form"] == "990-PF" for h in history)
    grants = db.grants_for_funder(ein)
    people = db.people_for(ein)
    filings_indexed = db.indexed_filings_for(ein)
    in_pipeline = db.pipeline_find_match(ein=ein, name=org["name"] if org else None)
    org_state = (org.get("state") or "").upper() if org else ""
    return render_template("org.html", ein=ein, org=org, error=error, history=history,
                           is_foundation=is_foundation, grants=grants, people=people,
                           filings_indexed=filings_indexed, in_pipeline=in_pipeline,
                           org_state=org_state, home_state=HOME_STATE,
                           out_of_state=bool(org_state) and org_state != HOME_STATE,
                           statuses=db.PIPELINE_STATUSES)


@app.route("/org/<ein>/hubspot.csv")
def org_hubspot_row(ein):
    """One funder as a HubSpot-import row, so a search finding never gets retyped."""
    ein = str(ein).replace("-", "")
    try:
        data = propublica.get_org(ein)
    except Exception:
        data = None
    if not data:
        flash("Could not load that organization.")
        return redirect(url_for("org_profile", ein=ein))
    org = data["organization"]
    history = propublica.financial_history(data)
    latest_pf = next((h for h in history if h["form"] == "990-PF"), None)
    notes = [f"{org.get('city','')}, {org.get('state','')}".strip(", ")]
    if latest_pf:
        notes.append("Private foundation (990-PF)")
        if latest_pf["grants_paid"]:
            notes.append(f"{latest_pf['grants_paid_source'].capitalize()} "
                         f"{money(latest_pf['grants_paid'])} ({latest_pf['year']})")
    notes.append(f"https://projects.propublica.org/nonprofits/organizations/{ein}")
    row = {
        "name": org.get("name"),
        "status": "Researching",
        "ein": ein,
        "notes": " · ".join(n for n in notes if n),
    }
    return _pipeline_hubspot_csv([row], filename=f"{ein}-hubspot.csv")


# ---------------- Local grants database ----------------

@app.route("/grants")
def grants():
    q = request.args.get("q", "").strip()
    state = request.args.get("state", "").strip()
    year = request.args.get("year", "").strip()
    min_amount = request.args.get("min_amount", "").strip()
    max_amount = request.args.get("max_amount", "").strip()
    funder_ein = request.args.get("funder_ein", "").strip()
    sort = request.args.get("sort", "amount_desc").strip()
    preset = request.args.get("preset", "")
    if preset == "recent":
        sort = "recent"
    year_n = int(year) if year.isdigit() else None
    min_n = int(min_amount) if min_amount.isdigit() else None
    max_n = int(max_amount) if max_amount.isdigit() else None
    if sort not in db.GRANT_SORTS:
        sort = "amount_desc"
    if preset == "vision" and not q:
        rows = [g for g in db.search_grants(state=state or None, year=year_n, sort=sort, limit=5000)
                if is_vision_match(g["purpose"], g["recipient_name"])][:300]
    else:
        rows = db.search_grants(q=q or None, state=state or None, year=year_n,
                                min_amount=min_n, max_amount=max_n,
                                funder_ein=funder_ein or None, sort=sort, limit=300)
    stats = db.grants_stats()
    return render_template("grants.html", rows=rows, q=q, state=state, year=year,
                           min_amount=min_amount, max_amount=max_amount, funder_ein=funder_ein,
                           sort=sort, preset=preset, stats=stats,
                           states=propublica.US_STATES, funders=db.funders_list())


# ---------------- Indexer ----------------

@app.route("/indexer")
def indexer_page():
    return render_template("indexer.html", seed=SEED_FUNDERS, status=indexer.status(),
                           stats=db.grants_stats())


@app.route("/indexer/add", methods=["POST"])
def indexer_add():
    ein = request.form.get("ein", "").strip().replace("-", "")
    name = request.form.get("name", "").strip()
    if ein.isdigit():
        indexer.enqueue(ein, name)
        flash(f"Queued {name or ein} for indexing.")
    else:
        flash("Please enter a valid EIN (numbers only).")
    return redirect(request.form.get("next") or url_for("indexer_page"))


@app.route("/indexer/add-many", methods=["POST"])
def indexer_add_many():
    """Queue several funders at once, straight from a page of search results —
    researching 20 candidates shouldn't mean 20 clicks and 20 waits."""
    pairs = request.form.getlist("selected")   # "EIN|Name" per checked result
    queued = 0
    for pair in pairs:
        ein, _, name = pair.partition("|")
        ein = ein.strip().replace("-", "")
        if ein.isdigit() and indexer.enqueue(ein, name.strip()):
            queued += 1
    if queued:
        flash(f"Queued {queued} funder(s) for indexing — this runs in the background.")
    else:
        flash("Nothing new to queue (select some results first).")
    return redirect(request.form.get("next") or url_for("search"))


@app.route("/indexer/seed", methods=["POST"])
def indexer_seed():
    for f in SEED_FUNDERS:
        indexer.enqueue(f["ein"], f["name"])
    flash(f"Queued all {len(SEED_FUNDERS)} starter funders. Indexing runs in the background.")
    return redirect(url_for("indexer_page"))


@app.route("/indexer/status")
def indexer_status():
    return jsonify(indexer.status())


# ---------------- Pipeline ----------------

# @app.route("/pipeline")
# def pipeline():
#     rows = db.pipeline_all()
#     return render_template("pipeline.html", rows=rows, statuses=db.PIPELINE_STATUSES)

@app.route("/pipeline")
def pipeline():
    rows = db.pipeline_all()
    # Attach username to each row
    rows = [dict(row) for row in rows]
    for row in rows:
        row["created_by_username"] = db.get_username_by_id(row["created_by_user_id"]) if row["created_by_user_id"] else "Unknown"
    return render_template("pipeline.html", rows=rows, statuses=db.PIPELINE_STATUSES)

# @app.route("/pipeline/add", methods=["POST"])
# def pipeline_add():
#     user_id = session.get("user_id")
#     if not user_id:
#         flash("You must be logged in to add to pipeline.")
#         return redirect(url_for("login"))
    
#     name = request.form.get("name", "").strip()
#     ein = request.form.get("ein", "").strip().replace("-", "") or None
#     if not name:
#         flash("A funder name is required.")
#         return redirect(request.form.get("next") or url_for("pipeline"))
#     if ein and db.pipeline_has_ein(ein):
#         flash(f"{name} is already in the shared pipeline.")
#     else:
#         db.pipeline_add(ein, name,
#                         status=request.form.get("status", "Researching"),
#                         ask_amount=request.form.get("ask_amount", ""),
#                         deadline=request.form.get("deadline", ""),
#                         contact=request.form.get("contact", ""),
#                         notes=request.form.get("notes", ""),
#                         created_by_user_id=user_id)
#         flash(f"Added {name} to the shared pipeline.")
#     return redirect(request.form.get("next") or url_for("pipeline"))


@app.route("/pipeline/<int:pid>/update", methods=["POST"])
def pipeline_update(pid):
    # Only update fields the submitting form actually carried, so the compact edit
    # form on one page can't blank out a field that only the other page shows.
    fields = {k: request.form.get(k) for k in
              ("status", "ask_amount", "deadline", "contact", "notes", "eligibility_notes")
              if k in request.form}
    db.pipeline_update(pid, **fields)
    flash("Prospect updated.")
    row = db.pipeline_get(pid)
    dest = "pipeline_team" if row and row["visibility"] == "team" else "pipeline_personal"
    return redirect(request.form.get("next") or url_for(dest))


@app.route("/pipeline/<int:pid>/delete", methods=["POST"])
def pipeline_delete(pid):
    row = db.pipeline_get(pid)
    db.pipeline_delete(pid)
    flash(f"Removed {row['name'] if row else 'prospect'} from pipeline.")
    return redirect(url_for("pipeline"))

# ---------- Pipeline: Team & Personal ----------------

@app.route("/pipeline/team")
def pipeline_team():
    """Shared team pipeline (visible to all)."""
    rows = db.pipeline_all_team()
    rows = [dict(row) for row in rows]
    for row in rows:
        row["created_by_username"] = db.get_username_by_id(row["created_by_user_id"]) if row["created_by_user_id"] else "Unknown"
    return render_template("pipeline_team.html", rows=rows, statuses=db.PIPELINE_STATUSES)

@app.route("/pipeline/personal")
def pipeline_personal():
    """Personal research pipeline (only visible to you)."""
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))
    rows = db.pipeline_all_personal(user_id)
    return render_template("pipeline_personal.html", rows=rows, statuses=db.PIPELINE_STATUSES)


HUBSPOT_COLUMNS = ["Deal Name", "Deal Stage", "Amount", "Close Date", "Deal Owner",
                   "Funder EIN", "Contact", "Notes", "Eligibility Notes", "Create Date"]


def _hubspot_date(value):
    """Reformat a YYYY-MM-DD (or epoch-seconds) value to HubSpot's default MM/DD/YYYY import format."""
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)):
            d = datetime.datetime.fromtimestamp(value).date()
        else:
            d = datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        return d.strftime("%m/%d/%Y")
    except (ValueError, TypeError):
        return str(value)


def _hubspot_amount(value):
    """Strip a free-text ask amount (e.g. "$10,000") down to digits HubSpot's Amount field accepts."""
    if not value:
        return ""
    digits = "".join(c for c in str(value) if c.isdigit() or c == ".")
    return digits or str(value)


def _pipeline_hubspot_csv(rows, filename="eyespy-pipeline-hubspot.csv"):
    """Build a HubSpot-deal-import-ready CSV for a set of pipeline rows.

    Columns match common HubSpot deal-import headers (Deal Name, Deal Stage, Amount,
    Close Date, Deal Owner...) so they auto-map during HubSpot's CSV import wizard.
    Deal Stage is exported as our own pipeline status text — create a custom "Grant
    Prospecting" deal pipeline in HubSpot with matching stage names before importing.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HUBSPOT_COLUMNS)
    for p in rows:
        p = dict(p)
        owner = db.get_username_by_id(p.get("created_by_user_id")) if p.get("created_by_user_id") else ""
        writer.writerow([
            p.get("name") or "",
            p.get("status") or "",
            _hubspot_amount(p.get("ask_amount")),
            _hubspot_date(p.get("deadline")),
            owner or "",
            p.get("ein") or "",
            p.get("contact") or "",
            p.get("notes") or "",
            p.get("eligibility_notes") or "",
            _hubspot_date(p.get("created_at")),
        ])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/pipeline/team/export/hubspot.csv")
def pipeline_team_export_hubspot():
    return _pipeline_hubspot_csv(db.pipeline_all_team())


@app.route("/pipeline/personal/export/hubspot.csv")
def pipeline_personal_export_hubspot():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))
    return _pipeline_hubspot_csv(db.pipeline_all_personal(user_id))

# Accept the export's own headers plus the column names HubSpot and the team's
# sheet actually use, so a round-trip (export -> HubSpot -> re-export -> import)
# works without hand-editing the file.
IMPORT_ALIASES = {
    "name": ["deal name", "name", "funder", "funder name", "organization", "company name", "company"],
    "status": ["deal stage", "status", "stage"],
    "ask_amount": ["amount", "ask amount", "ask", "deal amount"],
    "deadline": ["close date", "deadline", "due date"],
    "ein": ["funder ein", "ein", "tax id"],
    "contact": ["contact", "contact name", "primary contact"],
    "notes": ["notes", "description", "note"],
    "eligibility_notes": ["eligibility notes", "eligibility", "geographic eligibility"],
}


def _map_import_row(row):
    """Map one CSV row onto pipeline fields using case-insensitive header aliases."""
    lower = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
    out = {}
    for field, names in IMPORT_ALIASES.items():
        out[field] = next((lower[n] for n in names if lower.get(n)), "")
    return out


def _normalize_import_date(value):
    """Accept MM/DD/YYYY (HubSpot's default) or YYYY-MM-DD; store YYYY-MM-DD."""
    value = (value or "").strip()
    if not value:
        return ""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


@app.route("/pipeline/import", methods=["POST"])
def pipeline_import():
    """Import the team's HubSpot deal export so search results can be deduped
    against work the team has already done outside this app."""
    user_id = session.get("user_id")
    if not user_id:
        flash("You must be logged in to import.")
        return redirect(url_for("login"))

    upload = request.files.get("file")
    visibility = "team" if request.form.get("visibility") == "team" else "personal"
    if not upload or not upload.filename:
        flash("Choose a CSV file to import.")
        return redirect(url_for("pipeline_" + visibility))

    try:
        # Postgres text columns reject NUL bytes, which a mis-exported file can carry.
        text = upload.read().decode("utf-8-sig", errors="replace").replace("\x00", "")
    except Exception as e:
        flash(f"Could not read that file: {e}")
        return redirect(url_for("pipeline_" + visibility))

    added = skipped = invalid = 0
    # newline="" per the csv docs; without it an embedded newline aborts the parse.
    reader = csv.DictReader(io.StringIO(text, newline=""))
    try:
        rows = list(reader)
    except csv.Error as e:
        flash(f"That file isn’t readable as CSV ({e}). Export it again as CSV and retry.")
        return redirect(url_for("pipeline_" + visibility))

    for raw in rows:
        # A row with more fields than headers puts the overflow under None.
        raw.pop(None, None)
        row = _map_import_row(raw)
        if not row["name"]:
            invalid += 1
            continue
        ein = "".join(c for c in row["ein"] if c.isdigit()) or None
        if db.pipeline_find_match(ein=ein, name=row["name"]):
            skipped += 1
            continue
        status = row["status"] if row["status"] in db.PIPELINE_STATUSES else "Researching"
        db.pipeline_add(ein, row["name"], status=status,
                        ask_amount=row["ask_amount"],
                        deadline=_normalize_import_date(row["deadline"]),
                        contact=row["contact"], notes=row["notes"],
                        eligibility_notes=row["eligibility_notes"],
                        created_by_user_id=user_id, visibility=visibility,
                        source="hubspot-import")
        added += 1

    parts = [f"Imported {added} prospect(s)"]
    if skipped:
        parts.append(f"{skipped} already tracked (skipped)")
    if invalid:
        parts.append(f"{invalid} row(s) had no funder name")
    flash(". ".join(parts) + ".")
    return redirect(url_for("pipeline_" + visibility))


@app.route("/pipeline/add", methods=["POST"])
def pipeline_add():
    user_id = session.get("user_id")
    if not user_id:
        flash("You must be logged in.")
        return redirect(url_for("login"))
    
    name = request.form.get("name", "").strip()
    ein = request.form.get("ein", "").strip().replace("-", "") or None
    visibility = "team" if request.form.get("visibility") == "team" else "personal"

    if not name:
        flash("A funder name is required.")
        return redirect(request.form.get("next") or url_for("pipeline_personal"))

    existing = db.pipeline_find_match(ein=ein, name=name)
    if existing:
        flash(f"{name} is already tracked (added by "
              f"{db.get_username_by_id(existing['created_by_user_id']) or 'someone'}"
              f", status: {existing['status']}).")
        return redirect(request.form.get("next") or url_for("pipeline_" + visibility))

    db.pipeline_add(ein, name,
                    status=request.form.get("status", "Researching"),
                    ask_amount=request.form.get("ask_amount", ""),
                    deadline=request.form.get("deadline", ""),
                    contact=request.form.get("contact", ""),
                    notes=request.form.get("notes", ""),
                    eligibility_notes=request.form.get("eligibility_notes", ""),
                    created_by_user_id=user_id,
                    visibility=visibility,
                    source=request.form.get("source") or "app")

    flash(f"Added {name} to your {visibility} pipeline.")
    return redirect(request.form.get("next") or url_for("pipeline_" + visibility))

@app.route("/pipeline/<int:pid>/share-to-team", methods=["POST"])
def pipeline_share_to_team(pid):
    """Move prospect from personal to team."""
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))
    
    row = db.pipeline_get(pid)
    if not row or row["created_by_user_id"] != user_id:
        flash("You can only share your own items.")
        return redirect(url_for("pipeline_personal"))
    
    db.pipeline_share_to_team(pid)
    flash(f"'{row['name']}' shared with the team!")
    return redirect(url_for("pipeline_personal"))



def _already_running():
    # On Windows, binding an in-use port can silently succeed (SO_REUSEADDR),
    # so probe the port instead of relying on a bind error.
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 5001), timeout=1):
            return True
    except OSError:
        return False

# ---------- Authentication ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        if not all([username, email, password]):
            flash("All fields required.")
            return redirect(url_for("register"))
        uid = db.create_user(username, email, password)
        if uid:
            session["user_id"] = uid
            session["username"] = username
            flash(f"Welcome, {username}!")
            return redirect(url_for("dashboard"))
        else:
            flash("Username or email already exists.")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = db.get_user_by_username(username)
        if user and db.verify_password(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash(f"Welcome back, {username}!")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.")
    return redirect(url_for("login"))

@app.route("/api/token")
@auth.require_auth
def get_api_token():
    """Get API token for current user."""
    user_id = session.get("user_id") or request.user["id"]
    token = db.create_api_token(user_id)
    return jsonify({"token": token, "usage": "Add to requests: Authorization: Bearer " + token})


# ---------- Calendar & Deadlines ----------------

@app.route("/deadlines")
@auth.require_auth
def deadlines_view():
    """Calendar view of upcoming deadlines."""
    import datetime
    user_id = session.get("user_id")
    
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    today = datetime.date.today()
    
    if not year:
        year = today.year
    if not month:
        month = today.month
    
    deadlines = db.deadlines_by_month(user_id, year, month)
    upcoming = db.deadlines_upcoming(user_id, days_ahead=7)
    overdue = db.deadlines_overdue(user_id)
    
    # Build calendar grid
    import calendar
    cal = calendar.monthcalendar(year, month)
    deadline_map = {d["deadline"][:10]: d for d in deadlines}  # YYYY-MM-DD
    
    prev_month = (month - 2) % 12 + 1
    prev_year = year - 1 if month == 1 else year
    next_month = month % 12 + 1
    next_year = year + 1 if month == 12 else year
    
    return render_template("deadlines.html", 
                           year=year, month=month, 
                           month_name=calendar.month_name[month],
                           cal=cal, deadline_map=deadline_map,
                           upcoming=upcoming, overdue=overdue,
                           prev_year=prev_year, prev_month=prev_month,
                           next_year=next_year, next_month=next_month)


# ---------- Federal opportunities (grants.gov + NIH RePORTER) ----------------

@app.route("/opportunities")
def opportunities():
    """Browse federal funding opportunities.

    Defaults to browsing by agency rather than keyword: `agencies=["HHS-ACL"]`
    returns almost entirely relevant results, where the keyword "blind" mostly
    returns double-blind clinical trials.
    """
    q = request.args.get("q", "").strip()
    agencies = [a for a in request.args.getlist("agency") if a in grants_gov.AGENCIES]
    include_past = request.args.get("include_past") == "1"
    title_only = request.args.get("title_only", "1") == "1"
    preset = request.args.get("preset", "")
    searched = bool(q or agencies or preset)

    statuses = grants_gov.OPP_STATUS_ALL if include_past else grants_gov.OPP_STATUS_DEFAULT
    data, error = None, None
    if searched:
        try:
            if preset == "bvi":
                data = grants_gov.search_bvi(opp_statuses=statuses, rows=50)
            else:
                data = grants_gov.search(keyword=q, agencies=agencies or None,
                                         opp_statuses=statuses, rows=50,
                                         title_only=title_only)
        except Exception as e:
            error = f"Grants.gov search failed: {e}"

    # Group by program lineage so a ~5-year reissue cadence is visible at a glance.
    lineages = []
    if data and include_past:
        buckets = {}
        for row in data["results"]:
            buckets.setdefault(grants_gov.program_lineage(row["title"]), []).append(row)
        lineages = sorted([{"key": k, "rows": sorted(v, key=lambda r: r.get("open_date") or "", reverse=True)}
                           for k, v in buckets.items() if len(v) > 1],
                          key=lambda g: len(g["rows"]), reverse=True)

    saved = db.saved_searches_for(session["user_id"]) if session.get("user_id") else []
    return render_template("opportunities.html", q=q, agencies=agencies, data=data, error=error,
                           include_past=include_past, title_only=title_only, preset=preset,
                           searched=searched, agency_options=grants_gov.AGENCIES,
                           lineages=lineages, saved=saved, vision_match=is_vision_match)


@app.route("/opportunities/<opp_id>")
def opportunity_detail(opp_id):
    """Detail view: award ceiling/floor and applicant eligibility — the go/no-go
    fields that search results don't carry."""
    detail, error = None, None
    if not str(opp_id).isdigit():
        return render_template("opportunity.html", detail=None, in_pipeline=None,
                               statuses=db.PIPELINE_STATUSES,
                               error="That opportunity ID isn’t valid — Grants.gov IDs are numeric."), 404
    try:
        detail = grants_gov.fetch(opp_id)
    except Exception as e:
        error = f"Could not load that opportunity: {e}"
    in_pipeline = db.pipeline_find_match(name=detail["title"]) if detail else None
    return render_template("opportunity.html", detail=detail, error=error,
                           in_pipeline=in_pipeline, statuses=db.PIPELINE_STATUSES)


@app.route("/opportunities/saved", methods=["POST"])
def opportunities_save_search():
    """Watch a program family, so a reissue after a 5-year gap doesn't get missed."""
    user_id = session.get("user_id")
    if not user_id:
        flash("Log in to save a search.")
        return redirect(url_for("login"))
    keyword = request.form.get("q", "").strip()
    agencies = [a for a in request.form.getlist("agency") if a in grants_gov.AGENCIES]
    label = request.form.get("label", "").strip() or keyword or ", ".join(agencies) or "All opportunities"
    db.saved_search_add(user_id, label, keyword=keyword, agencies="|".join(agencies),
                        statuses=grants_gov.OPP_STATUS_DEFAULT,
                        title_only=request.form.get("title_only", "1") == "1")
    flash(f"Saved “{label}”. Check Watchlist to see new postings.")
    return redirect(url_for("opportunities_watchlist"))


@app.route("/opportunities/watchlist")
@auth.require_auth
def opportunities_watchlist():
    user_id = session.get("user_id")
    watches = []
    for s in db.saved_searches_for(user_id):
        seen = {i for i in (s["seen_ids"] or "").split(",") if i}
        rows, error = [], None
        try:
            out = grants_gov.search(keyword=s["keyword"] or "",
                                    agencies=(s["agencies"] or "").split("|") if s["agencies"] else None,
                                    opp_statuses=s["statuses"] or grants_gov.OPP_STATUS_DEFAULT,
                                    rows=25, title_only=bool(s["title_only"]))
            rows = out["results"]
        except Exception as e:
            error = str(e)
        for r in rows:
            r["is_new"] = bool(seen) and str(r["id"]) not in seen
        watches.append({"search": s, "rows": rows, "error": error,
                        "new_count": sum(1 for r in rows if r.get("is_new")),
                        "first_check": not seen})
        if rows:
            db.saved_search_mark_seen(s["id"], [r["id"] for r in rows])
    return render_template("watchlist.html", watches=watches)


@app.route("/opportunities/saved/<int:sid>/delete", methods=["POST"])
@auth.require_auth
def opportunities_delete_search(sid):
    db.saved_search_delete(sid, session.get("user_id"))
    flash("Saved search removed.")
    return redirect(url_for("opportunities_watchlist"))


# ---------- Health & error pages ----------------

@app.route("/healthz")
def healthz():
    """Cheap liveness endpoint. Point an external uptime pinger at this to keep the
    free-tier instance warm — a cold start is ~25s of blank screen for a board
    member or funder clicking the link."""
    return jsonify({"status": "ok", "time": datetime.datetime.now().isoformat()})


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    app.logger.exception("Unhandled error")
    return render_template("500.html"), 500


# ---------- REST API Endpoints ----------------

@app.route("/api/pipeline", methods=["GET", "POST"])
@auth.require_api_token
def api_pipeline():
    """Get or create pipeline entries via API."""
    user_id = request.user["id"]
    
    if request.method == "GET":
        rows = db.get_db().execute(
            "SELECT * FROM pipeline WHERE created_by_user_id=%s ORDER BY deadline ASC, updated_at DESC",
            (user_id,)).fetchall()
        return jsonify([dict(r) for r in rows])

    data = request.get_json()
    pid = db.pipeline_add(
        ein=data.get("ein"),
        name=data.get("name"),
        status=data.get("status", "Researching"),
        ask_amount=data.get("ask_amount", ""),
        deadline=data.get("deadline", ""),
        contact=data.get("contact", ""),
        notes=data.get("notes", ""),
        created_by_user_id=user_id
    )
    return jsonify({"id": pid}), 201

@app.route("/api/pipeline/<int:pid>", methods=["GET", "PUT", "DELETE"])
@auth.require_api_token
def api_pipeline_item(pid):
    """Get, update, or delete a specific pipeline entry."""
    user_id = request.user["id"]
    row = db.pipeline_get(pid)

    if not row or row["created_by_user_id"] != user_id:
        return jsonify({"error": "Not found"}), 404
    
    if request.method == "GET":
        return jsonify(dict(row))
    
    if request.method == "PUT":
        data = request.get_json()
        db.pipeline_update(pid, **data)
        return jsonify({"success": True})
    
    if request.method == "DELETE":
        db.pipeline_delete(pid)
        return jsonify({"success": True})

@app.route("/api/grants", methods=["GET"])
@auth.require_api_token
def api_grants():
    """Search grants via API."""
    q = request.args.get("q")
    state = request.args.get("state")
    min_amount = request.args.get("min_amount", type=int)
    max_amount = request.args.get("max_amount", type=int)
    year = request.args.get("year", type=int)
    
    rows = db.search_grants(q=q, state=state, min_amount=min_amount, 
                            max_amount=max_amount, year=year, limit=300)
    return jsonify([dict(r) for r in rows])

@app.route("/api/deadlines", methods=["GET"])
@auth.require_api_token
def api_deadlines():
    """Get upcoming deadlines via API."""
    user_id = request.user["id"]
    days = request.args.get("days_ahead", default=30, type=int)

    upcoming = db.deadlines_upcoming(user_id, days_ahead=days)
    overdue = db.deadlines_overdue(user_id)

    return jsonify({
        "upcoming": [dict(d) for d in upcoming],
        "overdue": [dict(d) for d in overdue]
    })


# Grants.gov and NIH RePORTER are public, read-only, unauthenticated government
# APIs. Gating our thin wrappers behind a token bought nothing and made the entire
# federal half of the research unreachable from the app, so these stay open — same
# as /search and /grants.
@app.route("/api/opportunities", methods=["GET"])
def api_opportunities():
    """Search federal grant opportunities (Grants.gov Search2 API)."""
    q = request.args.get("q", "").strip()
    status = request.args.get("status", grants_gov.OPP_STATUS_DEFAULT).strip()
    rows = request.args.get("rows", default=25, type=int)
    agencies = [a for a in request.args.getlist("agency") if a in grants_gov.AGENCIES]
    title_only = request.args.get("title_only", "0") == "1"
    try:
        data = grants_gov.search(keyword=q, opp_statuses=status, rows=min(rows, 100),
                                 agencies=agencies or None, title_only=title_only)
    except Exception as e:
        return jsonify({"error": f"Grants.gov search failed: {e}"}), 502
    return jsonify(data)


@app.route("/api/opportunities/<opp_id>", methods=["GET"])
def api_opportunity_detail(opp_id):
    """Award ceiling/floor, estimated funding and applicant eligibility for one
    opportunity (Grants.gov fetchOpportunity)."""
    if not str(opp_id).isdigit():
        return jsonify({"error": "opportunity id must be numeric"}), 400
    try:
        return jsonify(grants_gov.fetch(opp_id))
    except Exception as e:
        return jsonify({"error": f"Grants.gov fetch failed: {e}"}), 502


@app.route("/api/nih-projects", methods=["GET"])
def api_nih_projects():
    """Search awarded NIH research projects by keyword (NIH RePORTER API)."""
    q = request.args.get("q", "").strip()
    limit = request.args.get("limit", default=25, type=int)
    if not q:
        return jsonify({"error": "q (keyword) is required"}), 400
    try:
        data = nih_reporter.search(keyword=q, limit=min(limit, 100))
    except Exception as e:
        return jsonify({"error": f"NIH RePORTER search failed: {e}"}), 502
    return jsonify(data)



# ---------- First-run bootstrap ----------------

# If a boot is interrupted mid-index (routine on a free tier that spins down), the
# seed has to be retried — so the trigger is "the database is still empty", never a
# one-time "we queued it once" flag. The timestamp is only a backoff, so several
# workers restarting at once don't all queue the same work.
SEED_RETRY_SECONDS = 1800


def bootstrap_seed_index():
    """Index the curated starter funders in the background whenever the grants
    database is empty.

    Without this a brand-new deploy shows "0 grants from 0 indexed funders" and
    every Grants Database preset returns nothing — the feature that answers "which
    foundations actually fund BVI orgs" is dead on arrival for every new user.
    """
    try:
        if os.environ.get("SKIP_SEED_INDEX") == "1":
            return
        if db.grants_stats()["funders"] > 0:
            return   # real data present; nothing to bootstrap
        last = db.setting_get("seed_started_at")
        if last and time.time() - float(last) < SEED_RETRY_SECONDS:
            return   # a seed run is already in flight in this or another worker
        db.setting_set("seed_started_at", time.time())
        for f in SEED_FUNDERS:
            indexer.enqueue(f["ein"], f["name"])
        app.logger.info("Queued %d starter funders for first-run indexing.", len(SEED_FUNDERS))
    except Exception:
        # A bootstrap failure must never stop the app from serving.
        app.logger.exception("Seed indexing bootstrap failed")


_bootstrap_done = False


@app.before_request
def _bootstrap_once():
    """Run the seed check on the first served request rather than at import, so it
    only fires in a process that is actually serving traffic."""
    global _bootstrap_done
    if not _bootstrap_done:
        _bootstrap_done = True
        bootstrap_seed_index()


if __name__ == "__main__":
    if _already_running():
        print()
        print("  The app is ALREADY RUNNING in another window.")
        print("  Just open your browser to:  http://127.0.0.1:5001")
        print("  (Press Enter to close this window.)")
        try:
            input()
        except EOFError:
            pass
    else:
        print()
        print("  Eye Spy Grant Scout")
        print("  Open your browser to:  http://127.0.0.1:5001")
        print()
        app.run(host="127.0.0.1", port=5001, debug=False)
