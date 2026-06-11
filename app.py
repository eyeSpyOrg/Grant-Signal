"""Eye Spy Grant Scout — a free, local funder-research tool built on public IRS 990 data.

Run:  python app.py   then open http://127.0.0.1:5000
Data: ProPublica Nonprofit Explorer API (no key) + IRS 990 e-file XML via the
      public GivingTuesday data lake on S3 (no key).
"""
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash

import db
import indexer
import propublica
import seed_funders
from seed_funders import EYESPY_EIN, EYESPY_NAME, SEED_FUNDERS, is_vision_match

app = Flask(__name__)
app.secret_key = "eyespy-grant-scout-local"  # local single-user app; not exposed to the internet


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
    stats = db.grants_stats()
    counts = db.pipeline_counts()
    prospects = db.pipeline_all()[:8]
    # vision-relevant grants in the local database
    vision_grants = [g for g in db.search_grants(limit=5000)
                     if is_vision_match(g["purpose"], g["recipient_name"])]
    eyespy = None
    try:
        data = propublica.get_org(EYESPY_EIN)
        if data:
            eyespy = {"org": data["organization"],
                      "history": propublica.financial_history(data)[:3]}
    except Exception:
        pass  # offline is fine; dashboard still renders
    return render_template("dashboard.html", stats=stats, counts=counts,
                           prospects=prospects, vision_grants=vision_grants[:10],
                           vision_total=len(vision_grants), eyespy=eyespy,
                           seed=SEED_FUNDERS,
                           statuses=db.PIPELINE_STATUSES)


# ---------------- Funder search (ProPublica) ----------------

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    state = request.args.get("state", "")
    ntee = request.args.get("ntee", "")
    page = int(request.args.get("page", 0))
    results = None
    error = None
    if q or state or ntee:
        try:
            results = propublica.search(q=q, state=state or None,
                                        ntee=int(ntee) if ntee else None, page=page)
        except Exception as e:
            error = f"Search failed (are you online?): {e}"
    pipeline_eins = {r["ein"] for r in db.pipeline_all() if r["ein"]}
    return render_template("search.html", q=q, state=state, ntee=ntee, page=page,
                           results=results, error=error,
                           states=propublica.US_STATES, ntee_categories=propublica.NTEE_CATEGORIES,
                           pipeline_eins=pipeline_eins)


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
    in_pipeline = db.pipeline_has_ein(ein)
    return render_template("org.html", ein=ein, org=org, error=error, history=history,
                           is_foundation=is_foundation, grants=grants, people=people,
                           filings_indexed=filings_indexed, in_pipeline=in_pipeline)


# ---------------- Local grants database ----------------

@app.route("/grants")
def grants():
    q = request.args.get("q", "").strip()
    state = request.args.get("state", "").strip()
    year = request.args.get("year", "").strip()
    min_amount = request.args.get("min_amount", "").strip()
    preset = request.args.get("preset", "")
    if preset == "vision" and not q:
        rows = [g for g in db.search_grants(state=state or None,
                                            year=int(year) if year else None, limit=5000)
                if is_vision_match(g["purpose"], g["recipient_name"])][:300]
    else:
        rows = db.search_grants(q=q or None, state=state or None,
                                year=int(year) if year else None,
                                min_amount=int(min_amount) if min_amount.isdigit() else None,
                                limit=300)
    stats = db.grants_stats()
    return render_template("grants.html", rows=rows, q=q, state=state, year=year,
                           min_amount=min_amount, preset=preset, stats=stats,
                           states=propublica.US_STATES)


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

@app.route("/pipeline")
def pipeline():
    rows = db.pipeline_all()
    return render_template("pipeline.html", rows=rows, statuses=db.PIPELINE_STATUSES)


@app.route("/pipeline/add", methods=["POST"])
def pipeline_add():
    name = request.form.get("name", "").strip()
    ein = request.form.get("ein", "").strip().replace("-", "") or None
    if not name:
        flash("A funder name is required.")
        return redirect(request.form.get("next") or url_for("pipeline"))
    if ein and db.pipeline_has_ein(ein):
        flash(f"{name} is already in your pipeline.")
    else:
        db.pipeline_add(ein, name,
                        status=request.form.get("status", "Researching"),
                        ask_amount=request.form.get("ask_amount", ""),
                        deadline=request.form.get("deadline", ""),
                        contact=request.form.get("contact", ""),
                        notes=request.form.get("notes", ""))
        flash(f"Added {name} to your pipeline.")
    return redirect(request.form.get("next") or url_for("pipeline"))


@app.route("/pipeline/<int:pid>/update", methods=["POST"])
def pipeline_update(pid):
    db.pipeline_update(pid,
                       status=request.form.get("status"),
                       ask_amount=request.form.get("ask_amount", ""),
                       deadline=request.form.get("deadline", ""),
                       contact=request.form.get("contact", ""),
                       notes=request.form.get("notes", ""))
    flash("Prospect updated.")
    return redirect(url_for("pipeline"))


@app.route("/pipeline/<int:pid>/delete", methods=["POST"])
def pipeline_delete(pid):
    row = db.pipeline_get(pid)
    db.pipeline_delete(pid)
    flash(f"Removed {row['name'] if row else 'prospect'} from pipeline.")
    return redirect(url_for("pipeline"))


if __name__ == "__main__":
    print()
    print("  Eye Spy Grant Scout")
    print("  Open your browser to:  http://127.0.0.1:5000")
    print()
    app.run(host="127.0.0.1", port=5000, debug=False)
