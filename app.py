"""Eye Spy Grant Scout — a free, local funder-research tool built on public IRS 990 data.

Run:  python app.py   then open http://127.0.0.1:5000
Data: ProPublica Nonprofit Explorer API (no key) + IRS 990 e-file XML via the
      public GivingTuesday data lake on S3 (no key).
"""
import os

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask import session, redirect
import auth
import db
import indexer
import propublica
import seed_funders
from seed_funders import EYESPY_EIN, EYESPY_NAME, SEED_FUNDERS, is_vision_match

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "eyespy-grant-scout-dev")

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

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    state = request.args.get("state", "")
    ntee = request.args.get("ntee", "")
    page_raw = request.args.get("page", "0")
    page = int(page_raw) if page_raw.isdigit() else 0
    results = None
    error = None
    if q or state or ntee:
        try:
            results = propublica.search(q=q, state=state or None,
                                        ntee=int(ntee) if ntee.isdigit() else None, page=page)
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

@app.route("/pipeline/add", methods=["POST"])
def pipeline_add():
    user_id = session.get("user_id")
    if not user_id:
        flash("You must be logged in.")
        return redirect(url_for("login"))
    
    name = request.form.get("name", "").strip()
    ein = request.form.get("ein", "").strip().replace("-", "") or None
    visibility = request.form.get("visibility", "personal")  # personal or team
    
    if not name:
        flash("A funder name is required.")
        return redirect(request.form.get("next") or url_for("pipeline_personal"))
    
    pid = db.pipeline_add(ein, name,
                    status=request.form.get("status", "Researching"),
                    ask_amount=request.form.get("ask_amount", ""),
                    deadline=request.form.get("deadline", ""),
                    contact=request.form.get("contact", ""),
                    notes=request.form.get("notes", ""),
                    created_by_user_id=user_id)
    
    # Set visibility
    db.get_db().execute("UPDATE pipeline SET visibility=%s WHERE id=%s", (visibility, pid))
    db.get_db().commit()
    
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
