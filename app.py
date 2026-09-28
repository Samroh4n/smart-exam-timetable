"""Smart Exam Timetable Scheduling - Flask application (routes, auth, exports)."""
import csv
import io
import os
import re
import secrets
from datetime import datetime
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required, login_user,
                         logout_user)
from werkzeug.security import check_password_hash, generate_password_hash

import database as db
import scheduler
import seed_data
import validators

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(BASE_DIR, "exports")


def load_env_file():
    """Minimal .env reader so FLASK_SECRET_KEY can live in a git-ignored file."""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()
app = Flask(__name__)
_secret = os.environ.get("FLASK_SECRET_KEY")
if not _secret or _secret == "replace-this-with-a-random-secret-key":
    _secret = secrets.token_hex(32)
    print("Note: FLASK_SECRET_KEY not set; using a temporary key (sessions reset on restart).")
app.config.update(SECRET_KEY=_secret, MAX_CONTENT_LENGTH=2 * 1024 * 1024,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "warning"


# ----------------------------------------------------------------- helpers
def get_db():
    if "db" not in g:
        g.db = db.connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


class User(UserMixin):
    def __init__(self, row):
        self.id = row["user_id"]
        self.username = row["username"]
        self.role = row["role"]
        self.student_id = row["student_id"]

    def get_id(self):
        return str(self.id)

    @property
    def is_admin(self):
        return self.role == "admin"


@login_manager.user_loader
def load_user(user_id):
    row = db.query_one(get_db(), "SELECT * FROM users WHERE user_id = ?", (user_id,))
    return User(row) if row else None


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def student_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != "student":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            flash("Your form expired. Please try again.", "error")
            return redirect(request.referrer or url_for("index"))


@app.template_filter("runtime")
def fmt_runtime(seconds):
    if seconds is None:
        return "-"
    return f"{seconds * 1000:.3f} ms" if seconds < 1 else f"{seconds:.3f} s"


@app.errorhandler(403)
def forbidden(_e):
    return render_template("login.html", error_page="You do not have permission to open that page."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("login.html", error_page="That page or record was not found."), 404


@app.errorhandler(413)
def too_large(_e):
    flash("That file is larger than the 2 MB upload limit.", "error")
    return redirect(url_for("data_management"))


def parse_time_limit(raw, default=10.0):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, 0.01), 300.0)


def get_run(conn, run_id):
    return db.query_one(conn, "SELECT * FROM timetable_runs WHERE run_id = ?", (run_id,))


def timetable_rows(conn, run_id, student_id=None):
    sql = ("SELECT s.subject_code, s.subject_name, s.enrollment_count, t.exam_date, t.start_time, "
           "t.end_time, e.slot_id, r.room_name, r.capacity, e.assigned_color "
           "FROM timetable_entries e JOIN subjects s ON s.subject_id = e.subject_id "
           "LEFT JOIN time_slots t ON t.slot_id = e.slot_id LEFT JOIN rooms r ON r.room_id = e.room_id "
           "WHERE e.run_id = ? ")
    params = [run_id]
    if student_id:
        sql += "AND e.subject_id IN (SELECT subject_id FROM registrations WHERE student_id = ?) "
        params.append(student_id)
    sql += "ORDER BY t.exam_date, t.start_time, s.subject_code"
    return db.query_all(conn, sql, params)


def run_validation(conn, run_id):
    return validators.validate_run(conn, run_id, scheduler.build_conflict_graph(conn))


# -------------------------------------------------------------------- auth
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("admin_dashboard" if current_user.is_admin else "student_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            error = "Enter both a username and a password."
        else:
            row = db.query_one(get_db(), "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,))
            if row is None or not check_password_hash(row["password_hash"], password):
                error = "Incorrect username or password."
            else:
                session.pop("_csrf", None)
                login_user(User(row))
                return redirect(url_for("index"))
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))


# ------------------------------------------------------------- dashboards
@app.route("/admin")
@admin_required
def admin_dashboard():
    conn = get_db()
    graph = scheduler.build_conflict_graph(conn)
    stats = {
        "students": db.scalar(conn, "SELECT COUNT(*) FROM students"),
        "subjects": db.scalar(conn, "SELECT COUNT(*) FROM subjects"),
        "rooms": db.scalar(conn, "SELECT COUNT(*) FROM rooms"),
        "slots": db.scalar(conn, "SELECT COUNT(*) FROM time_slots"),
        "edges": graph.number_of_edges(),
    }
    return render_template("admin_dashboard.html", stats=stats, latest=db.latest_completed_run(conn))


@app.route("/student")
@student_required
def student_dashboard():
    conn = get_db()
    student = db.query_one(conn, "SELECT * FROM students WHERE student_id = ?", (current_user.student_id,))
    subjects, rows, run = [], [], db.latest_completed_run(conn)
    if student:
        subjects = db.query_all(
            conn, "SELECT s.* FROM registrations r JOIN subjects s ON s.subject_id = r.subject_id "
                  "WHERE r.student_id = ? ORDER BY s.subject_code", (student["student_id"],))
        if run:
            rows = timetable_rows(conn, run["run_id"], student["student_id"])
    return render_template("student_dashboard.html", student=student, subjects=subjects, rows=rows, run=run)


# ---------------------------------------------------------- data management
TABLES = {
    "students": {
        "table": "students", "pk": "student_id", "label": "Students",
        "fields": [("student_id", "id"), ("student_name", "name")],
        "select": ("SELECT s.student_id, s.student_name, CASE WHEN u.user_id IS NULL THEN 'No' ELSE 'Yes' END "
                   "AS has_login FROM students s LEFT JOIN users u ON u.student_id = s.student_id "
                   "AND u.role = 'student'"),
        "columns": [("student_id", "Student ID"), ("student_name", "Name"), ("has_login", "Has login")],
        "search": ["s.student_id", "s.student_name"], "order": "s.student_id", "alias": "s"},
    "subjects": {
        "table": "subjects", "pk": "subject_id", "label": "Subjects",
        "fields": [("subject_id", "id"), ("subject_code", "id"), ("subject_name", "name"),
                   ("duration_minutes", "duration")],
        "select": "SELECT * FROM subjects s",
        "columns": [("subject_id", "Subject ID"), ("subject_code", "Code"), ("subject_name", "Name"),
                    ("duration_minutes", "Minutes"), ("enrollment_count", "Enrolled")],
        "search": ["s.subject_id", "s.subject_code", "s.subject_name"], "order": "s.subject_id", "alias": "s"},
    "registrations": {
        "table": "registrations", "pk": "registration_id", "label": "Registrations",
        "fields": [("student_id", "id"), ("subject_id", "id")],
        "select": ("SELECT r.registration_id, r.student_id, r.subject_id, s.subject_code FROM registrations r "
                   "LEFT JOIN subjects s ON s.subject_id = r.subject_id"),
        "columns": [("registration_id", "Reg. no."), ("student_id", "Student ID"),
                    ("subject_id", "Subject ID"), ("subject_code", "Subject code")],
        "search": ["r.student_id", "r.subject_id", "s.subject_code"], "order": "r.registration_id", "alias": "r"},
    "rooms": {
        "table": "rooms", "pk": "room_id", "label": "Rooms",
        "fields": [("room_id", "id"), ("room_name", "name"), ("capacity", "capacity")],
        "select": "SELECT * FROM rooms r",
        "columns": [("room_id", "Room ID"), ("room_name", "Name"), ("capacity", "Capacity")],
        "search": ["r.room_id", "r.room_name"], "order": "r.room_id", "alias": "r"},
    "slots": {
        "table": "time_slots", "pk": "slot_id", "label": "Time slots",
        "fields": [("slot_id", "id"), ("exam_date", "date"), ("start_time", "time"), ("end_time", "time")],
        "select": "SELECT * FROM time_slots t",
        "columns": [("slot_id", "Slot ID"), ("exam_date", "Date"), ("start_time", "Start"), ("end_time", "End")],
        "search": ["t.slot_id", "t.exam_date"], "order": "t.exam_date, t.start_time", "alias": "t"},
}
FIELD_LABELS = {"student_id": "Student ID", "student_name": "Student name", "subject_id": "Subject ID",
                "subject_code": "Subject code", "subject_name": "Subject name",
                "duration_minutes": "Duration (minutes)", "room_id": "Room ID", "room_name": "Room name",
                "capacity": "Capacity", "slot_id": "Slot ID", "exam_date": "Exam date (YYYY-MM-DD)",
                "start_time": "Start time (HH:MM)", "end_time": "End time (HH:MM)"}
INPUT_TYPES = {"date": "date", "time": "time", "duration": "number", "capacity": "number"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,20}$")


def clean_field(field, kind, raw):
    """Return a cleaned value or raise ValueError with a readable message."""
    label = FIELD_LABELS[field]
    value = (raw or "").strip()
    if not value:
        raise ValueError(f"{label} is required.")
    if kind == "id":
        if not ID_PATTERN.match(value):
            raise ValueError(f"{label} may only use letters, digits, '-' and '_' (max 20 characters).")
        return value
    if kind == "name":
        if len(value) > 100:
            raise ValueError(f"{label} must be 100 characters or fewer.")
        return value
    if kind in ("duration", "capacity"):
        try:
            number = int(value)
        except ValueError:
            raise ValueError(f"{label} must be a whole number.")
        low, high = (30, 480) if kind == "duration" else (1, 1000)
        if not low <= number <= high:
            raise ValueError(f"{label} must be between {low} and {high}.")
        return number
    if kind == "date":
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise ValueError(f"{label} must be a valid date like 2026-01-15.")
    if kind == "time":
        try:
            return datetime.strptime(value, "%H:%M").strftime("%H:%M")
        except ValueError:
            raise ValueError(f"{label} must be a valid 24-hour time like 09:00.")
    raise ValueError(f"Unsupported field {label}.")


def validate_record(conn, key, data, editing_pk=None, csv_mode=False):
    cfg = TABLES[key]
    clean, errors = {}, []
    for field, kind in cfg["fields"]:
        try:
            clean[field] = clean_field(field, kind, data.get(field))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        return None, errors
    if key == "slots" and clean["end_time"] <= clean["start_time"]:
        errors.append("End time must be later than start time.")
    if key == "registrations":
        if not db.query_one(conn, "SELECT 1 FROM students WHERE student_id = ?", (clean["student_id"],)):
            errors.append(f"Student {clean['student_id']} does not exist.")
        if not db.query_one(conn, "SELECT 1 FROM subjects WHERE subject_id = ?", (clean["subject_id"],)):
            errors.append(f"Subject {clean['subject_id']} does not exist.")
        dup = db.query_one(conn, "SELECT registration_id FROM registrations WHERE student_id = ? AND subject_id = ?",
                           (clean["student_id"], clean["subject_id"]))
        if dup and str(dup["registration_id"]) != str(editing_pk):
            errors.append("That student is already registered for that subject.")
    elif not csv_mode and cfg["pk"] in clean and not editing_pk:
        if db.query_one(conn, f"SELECT 1 FROM {cfg['table']} WHERE {cfg['pk']} = ?", (clean[cfg["pk"]],)):
            errors.append(f"{FIELD_LABELS[cfg['pk']]} {clean[cfg['pk']]} already exists.")
    return (None if errors else clean), errors


def delete_record(conn, key, pk):
    cfg = TABLES[key]
    if key == "students":
        conn.execute("DELETE FROM registrations WHERE student_id = ?", (pk,))
        conn.execute("DELETE FROM users WHERE student_id = ? AND role = 'student'", (pk,))
    elif key == "subjects":
        conn.execute("DELETE FROM registrations WHERE subject_id = ?", (pk,))
        conn.execute("DELETE FROM timetable_entries WHERE subject_id = ?", (pk,))
    elif key == "rooms":
        conn.execute("UPDATE timetable_entries SET room_id = NULL WHERE room_id = ?", (pk,))
    conn.execute(f"DELETE FROM {cfg['table']} WHERE {cfg['pk']} = ?", (pk,))
    db.refresh_enrollment(conn)
    conn.commit()


def render_data_page(tab, form_values=None, editing_pk=None, form_errors=None):
    conn = get_db()
    cfg = TABLES[tab]
    q = request.args.get("q", "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    per_page = 50
    where, params = "", []
    if q:
        where = " WHERE (" + " OR ".join(f"{c} LIKE ?" for c in cfg["search"]) + ")"
        params = [f"%{q}%"] * len(cfg["search"])
    total = db.scalar(conn, f"SELECT COUNT(*) FROM ({cfg['select']}{where})", params)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = db.query_all(conn, f"{cfg['select']}{where} ORDER BY {cfg['order']} LIMIT ? OFFSET ?",
                        params + [per_page, (page - 1) * per_page])
    edit_pk = editing_pk or request.args.get("edit")
    if form_values is None and edit_pk:
        row = db.query_one(conn, f"SELECT * FROM {cfg['table']} WHERE {cfg['pk']} = ?", (edit_pk,))
        if row:
            form_values = dict(row)
        else:
            edit_pk = None
    counts = {k: db.scalar(conn, f"SELECT COUNT(*) FROM {c['table']}") for k, c in TABLES.items()}
    return render_template("data_management.html", tab=tab, cfg=cfg, tables=TABLES, rows=rows, q=q, page=page,
                           pages=pages, total=total, form_values=form_values or {}, edit_pk=edit_pk,
                           form_errors=form_errors or [], counts=counts, labels=FIELD_LABELS,
                           input_types=INPUT_TYPES)


@app.route("/admin/data")
@admin_required
def data_management():
    tab = request.args.get("tab", "students")
    if tab not in TABLES:
        tab = "students"
    return render_data_page(tab)


@app.route("/admin/data/<key>/save", methods=["POST"])
@admin_required
def data_save(key):
    if key not in TABLES:
        abort(404)
    cfg, conn = TABLES[key], get_db()
    original = request.form.get("original_pk", "").strip()
    data = {f: request.form.get(f, "") for f, _ in cfg["fields"]}
    keyed = cfg["pk"] in data
    if original and keyed:
        data[cfg["pk"]] = original  # primary keys cannot be changed when editing
    clean, errors = validate_record(conn, key, data, editing_pk=original or None)
    if errors:
        return render_data_page(key, form_values=data, editing_pk=original or None, form_errors=errors)
    fields = [f for f, _ in cfg["fields"]]
    if original:
        sets = [f for f in fields if f != cfg["pk"]]
        conn.execute(f"UPDATE {cfg['table']} SET " + ", ".join(f"{f} = ?" for f in sets) +
                     f" WHERE {cfg['pk']} = ?", [clean[f] for f in sets] + [original])
        flash("Record updated.", "success")
    else:
        conn.execute(f"INSERT INTO {cfg['table']}(" + ", ".join(fields) + ") VALUES(" +
                     ", ".join("?" for _ in fields) + ")", [clean[f] for f in fields])
        flash("Record added.", "success")
    db.refresh_enrollment(conn)
    conn.commit()
    return redirect(url_for("data_management", tab=key))


@app.route("/admin/data/<key>/delete/<pk>", methods=["POST"])
@admin_required
def data_delete(key, pk):
    if key not in TABLES:
        abort(404)
    delete_record(get_db(), key, pk)
    flash("Record deleted.", "success")
    return redirect(url_for("data_management", tab=key))


@app.route("/admin/data/<key>/template.csv")
@admin_required
def csv_template(key):
    if key not in TABLES:
        abort(404)
    fields = [f for f, _ in TABLES[key]["fields"]]
    example = {"students": ["ST301", "Student ST301"], "subjects": ["S26", "CS401", "Machine Learning", "120"],
               "registrations": ["ST301", "S01"], "rooms": ["R09", "Room 401", "70"],
               "slots": ["T11", "2026-01-15", "09:00", "12:00"]}[key]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(fields)
    writer.writerow(example)
    return send_file(io.BytesIO(buf.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True,
                     download_name=f"{key}_template.csv")


@app.route("/admin/data/<key>/upload", methods=["POST"])
@admin_required
def data_upload(key):
    if key not in TABLES:
        abort(404)
    cfg, conn = TABLES[key], get_db()
    back = redirect(url_for("data_management", tab=key))
    upload = request.files.get("csv_file")
    if not upload or not upload.filename:
        flash("Choose a CSV file to upload.", "error")
        return back
    if not upload.filename.lower().endswith(".csv"):
        flash("Only .csv files are accepted.", "error")
        return back
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("The file is not valid UTF-8 text. Save it as CSV (UTF-8) and try again.", "error")
        return back
    fields = [f for f, _ in cfg["fields"]]
    added = updated = skipped = 0
    problems = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            flash("The CSV file is empty.", "error")
            return back
        reader.fieldnames = [(h or "").strip().lower() for h in reader.fieldnames]
        missing = [f for f in fields if f not in reader.fieldnames]
        if missing:
            flash(f"Missing required column(s): {', '.join(missing)}. Expected header: {', '.join(fields)}.", "error")
            return back
        for line_no, row in enumerate(reader, start=2):
            if line_no > 5001:
                problems.append("Stopped at 5,000 rows (upload limit).")
                break
            data = {f: row.get(f) for f in fields}
            if not any((v or "").strip() for v in data.values()):
                continue
            clean, errors = validate_record(conn, key, data, csv_mode=True)
            if errors:
                skipped += 1
                problems.append(f"Line {line_no}: {' '.join(errors)}")
                continue
            if key == "registrations":
                cur = conn.execute("INSERT OR IGNORE INTO registrations(student_id, subject_id) VALUES(?,?)",
                                   (clean["student_id"], clean["subject_id"]))
                if cur.rowcount:
                    added += 1
                else:
                    skipped += 1
                    problems.append(f"Line {line_no}: duplicate registration ignored.")
                continue
            exists = db.query_one(conn, f"SELECT 1 FROM {cfg['table']} WHERE {cfg['pk']} = ?", (clean[cfg["pk"]],))
            sets = [f for f in fields if f != cfg["pk"]]
            if exists:
                conn.execute(f"UPDATE {cfg['table']} SET " + ", ".join(f"{f} = ?" for f in sets) +
                             f" WHERE {cfg['pk']} = ?", [clean[f] for f in sets] + [clean[cfg["pk"]]])
                updated += 1
            else:
                conn.execute(f"INSERT INTO {cfg['table']}(" + ", ".join(fields) + ") VALUES(" +
                             ", ".join("?" for _ in fields) + ")", [clean[f] for f in fields])
                added += 1
    except csv.Error as exc:
        conn.rollback()
        flash(f"The CSV could not be read: {exc}", "error")
        return back
    db.refresh_enrollment(conn)
    conn.commit()
    flash(f"CSV processed: {added} added, {updated} updated, {skipped} skipped.",
          "success" if added or updated else "warning")
    for p in problems[:10]:
        flash(p, "error")
    if len(problems) > 10:
        flash(f"...and {len(problems) - 10} more problem(s).", "error")
    return back


@app.route("/admin/students/login", methods=["POST"])
@admin_required
def student_login_create():
    conn = get_db()
    student_id = request.form.get("student_id", "").strip()
    password = request.form.get("password", "")
    back = redirect(url_for("data_management", tab="students"))
    if not db.query_one(conn, "SELECT 1 FROM students WHERE student_id = ?", (student_id,)):
        flash(f"Student {student_id or '(blank)'} does not exist.", "error")
        return back
    if len(password) < 6:
        flash("Use a password of at least 6 characters.", "error")
        return back
    existing = db.query_one(conn, "SELECT user_id, role FROM users WHERE username = ? COLLATE NOCASE", (student_id,))
    if existing and existing["role"] != "student":
        flash("That username belongs to a non-student account.", "error")
        return back
    if existing:
        conn.execute("UPDATE users SET password_hash = ? WHERE user_id = ?",
                     (generate_password_hash(password), existing["user_id"]))
    else:
        conn.execute("INSERT INTO users(username, password_hash, role, student_id) VALUES(?,?,?,?)",
                     (student_id, generate_password_hash(password), "student", student_id))
    conn.commit()
    flash(f"Login saved for {student_id}.", "success")
    return back


@app.route("/admin/reset-demo", methods=["POST"])
@admin_required
def reset_demo():
    seed_data.seed_demo(get_db())
    flash("Demo data restored: 25 subjects, 300 students, 8 rooms and 10 time slots. Old timetables were removed.",
          "success")
    return redirect(url_for("admin_dashboard"))


# ------------------------------------------------------------------- graph
@app.route("/admin/graph")
@admin_required
def conflict_graph():
    conn = get_db()
    graph = scheduler.build_conflict_graph(conn)
    n, m = graph.number_of_nodes(), graph.number_of_edges()
    info = {"nodes": n, "edges": m,
            "density": (2 * m / (n * (n - 1))) if n > 1 else 0,
            "max_degree": max((d for _, d in graph.degree), default=0)}
    return render_template("conflict_graph.html", info=info, latest=db.latest_completed_run(conn))


@app.route("/admin/graph-data")
@admin_required
def graph_data():
    conn = get_db()
    graph = scheduler.build_conflict_graph(conn)
    run = db.latest_completed_run(conn)
    colors = {}
    if run:
        colors = {e["subject_id"]: e["assigned_color"] for e in db.query_all(
            conn, "SELECT subject_id, assigned_color FROM timetable_entries WHERE run_id = ?", (run["run_id"],))}
    nodes = [{"id": n, "code": d["code"], "name": d["name"], "enrollment": d["enrollment"],
              "degree": graph.degree(n), "color": colors.get(n)} for n, d in graph.nodes(data=True)]
    edges = [{"source": u, "target": v, "weight": d.get("weight", 1)} for u, v, d in graph.edges(data=True)]
    return jsonify({"nodes": nodes, "edges": edges,
                    "algorithm": run["algorithm_name"] if run else None})


# ---------------------------------------------------------------- generate
@app.route("/admin/generate", methods=["GET", "POST"])
@admin_required
def generate():
    conn = get_db()
    selected, limit = "dsatur", 10.0
    if request.method == "POST":
        selected = request.form.get("algorithm", "")
        limit = parse_time_limit(request.form.get("time_limit"))
        if selected not in scheduler.ALGORITHMS:
            flash("Choose one of the five algorithms.", "error")
            selected = "dsatur"
        else:
            outcome = scheduler.generate_timetable(conn, selected, limit)
            if outcome["ok"]:
                flash(outcome["message"], "success" if outcome.get("valid") else "warning")
                return redirect(url_for("timetable", run_id=outcome["run_id"]))
            flash(outcome["message"], "error")
    runs = db.query_all(conn, "SELECT * FROM timetable_runs ORDER BY run_id DESC LIMIT 10")
    return render_template("generate.html", algorithms=scheduler.ALGORITHMS, selected=selected, limit=limit, runs=runs)


@app.route("/admin/timetable")
@app.route("/admin/timetable/<int:run_id>")
@admin_required
def timetable(run_id=None):
    conn = get_db()
    runs = db.query_all(conn, "SELECT * FROM timetable_runs WHERE status = 'completed' ORDER BY run_id DESC")
    if run_id is None:
        latest = db.latest_completed_run(conn)
        run_id = latest["run_id"] if latest else None
    run = get_run(conn, run_id) if run_id else None
    if run_id and (run is None or run["status"] != "completed"):
        abort(404)
    rows = timetable_rows(conn, run_id) if run else []
    report = run_validation(conn, run_id) if run else None
    return render_template("timetable.html", runs=runs, run=run, rows=rows, report=report)


# -------------------------------------------------------------- comparison
@app.route("/admin/compare", methods=["GET", "POST"])
@admin_required
def compare():
    conn = get_db()
    limit = parse_time_limit(request.values.get("time_limit"))
    rows = rec = None
    if request.method == "POST" or request.args.get("run") == "1":
        graph = scheduler.build_conflict_graph(conn)
        if graph.number_of_nodes() == 0:
            flash("There are no subjects to compare.", "error")
        else:
            rows, rec = scheduler.compare_algorithms(graph, limit)
    n_slots = db.scalar(conn, "SELECT COUNT(*) FROM time_slots")
    return render_template("comparison.html", rows=rows, rec=rec, limit=limit, n_slots=n_slots)


# -------------------------------------------------------------- validation
@app.route("/admin/validation")
@app.route("/admin/validation/<int:run_id>")
@admin_required
def validation(run_id=None):
    conn = get_db()
    runs = db.query_all(conn, "SELECT * FROM timetable_runs WHERE status = 'completed' ORDER BY run_id DESC")
    if run_id is None:
        latest = db.latest_completed_run(conn)
        run_id = latest["run_id"] if latest else None
    run = get_run(conn, run_id) if run_id else None
    if run_id and (run is None or run["status"] != "completed"):
        abort(404)
    report = run_validation(conn, run_id) if run else None
    return render_template("validation.html", runs=runs, run=run, report=report)


# ----------------------------------------------------------------- exports
def _time_range(r):
    return f"{r['start_time']}-{r['end_time']}" if r["start_time"] else "Missing slot"


def _table_data(rows):
    header = ["Code", "Subject", "Students", "Date", "Time", "Slot", "Room", "Capacity", "Color"]
    body = [[r["subject_code"], r["subject_name"], r["enrollment_count"], r["exam_date"] or "-",
             _time_range(r), r["slot_id"], r["room_name"] or "Unassigned",
             r["capacity"] if r["capacity"] is not None else "-", r["assigned_color"]] for r in rows]
    return header, body


def build_excel(run, rows, report):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Timetable"
    meta = [("Algorithm", run["algorithm_name"]), ("Generated at", run["generated_at"]),
            ("Slots used", run["slots_used"]), ("Runtime", fmt_runtime(run["runtime_seconds"])),
            ("Validation result", "PASS" if report["all_passed"] else "FAIL")]
    ws.append(["Smart Exam Timetable Scheduling"])
    ws["A1"].font = Font(bold=True, size=14)
    for k, v in meta:
        ws.append([k, v])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    ws.append([])
    header, body = _table_data(rows)
    ws.append(header)
    head_row = ws.max_row
    for c in ws[head_row]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(horizontal="center")
    for line in body:
        ws.append(line)
    for col, width in zip("ABCDEFGHI", (10, 32, 10, 12, 14, 8, 14, 10, 8)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = ws.cell(row=head_row + 1, column=1)
    vs = wb.create_sheet("Validation")
    vs.append(["Check", "Result", "Details"])
    for c in vs[1]:
        c.font = Font(bold=True)
    for chk in report["checks"]:
        vs.append([chk["name"], "PASS" if chk["passed"] else "FAIL", "; ".join(chk["details"])])
    vs.column_dimensions["A"].width = 55
    vs.column_dimensions["C"].width = 80
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_pdf(title, meta, rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm, title=title)
    story = [Paragraph(title, styles["Title"])]
    mt = Table([[k, str(v)] for k, v in meta], colWidths=[40 * mm, 120 * mm])
    mt.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2)]))
    story += [mt, Spacer(1, 6 * mm)]
    header, body = _table_data(rows)
    small = styles["BodyText"].clone("small")
    small.fontSize = 8
    small.leading = 10
    data = [header] + [[Paragraph(str(c), small) if i == 1 else c for i, c in enumerate(line)] for line in body]
    if not body:
        data.append(["No exams scheduled"] + [""] * 8)
    widths = [20, 70, 18, 24, 30, 14, 30, 18, 14]
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9AA5B1")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F4F8")])]))
    story.append(table)
    doc.build(story)
    return buf.getvalue()


def save_and_send(data, filename, mimetype):
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.join(EXPORT_DIR, filename)
    with open(path, "wb") as fh:
        fh.write(data)
    return send_file(path, mimetype=mimetype, as_attachment=True, download_name=filename)


def _export_context(run_id):
    conn = get_db()
    run = get_run(conn, run_id)
    if run is None or run["status"] != "completed":
        abort(404)
    return conn, run, timetable_rows(conn, run_id), run_validation(conn, run_id)


def _pdf_meta(run, report):
    return [("Algorithm", run["algorithm_name"]), ("Generated at", run["generated_at"]),
            ("Slots used", run["slots_used"]), ("Runtime", fmt_runtime(run["runtime_seconds"])),
            ("Validation result", "PASS" if report["all_passed"] else "FAIL")]


@app.route("/admin/export/<int:run_id>/excel")
@admin_required
def export_excel(run_id):
    _, run, rows, report = _export_context(run_id)
    return save_and_send(build_excel(run, rows, report), f"timetable_run{run_id}.xlsx",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/admin/export/<int:run_id>/pdf")
@admin_required
def export_pdf(run_id):
    _, run, rows, report = _export_context(run_id)
    return save_and_send(build_pdf("Exam Timetable - Full", _pdf_meta(run, report), rows),
                         f"timetable_run{run_id}.pdf", "application/pdf")


@app.route("/student/timetable.pdf")
@student_required
def student_pdf():
    conn = get_db()
    student = db.query_one(conn, "SELECT * FROM students WHERE student_id = ?", (current_user.student_id,))
    run = db.latest_completed_run(conn)
    if not student or not run:
        flash("No personal timetable is available yet.", "warning")
        return redirect(url_for("student_dashboard"))
    rows = timetable_rows(conn, run["run_id"], student["student_id"])
    report = run_validation(conn, run["run_id"])
    meta = [("Student", f"{student['student_name']} ({student['student_id']})")] + _pdf_meta(run, report)
    safe_id = re.sub(r"[^A-Za-z0-9_\-]", "", student["student_id"])
    return save_and_send(build_pdf("My Exam Timetable", meta, rows), f"timetable_{safe_id}.pdf", "application/pdf")


# ---------------------------------------------------------------- bootstrap
def bootstrap():
    db.init_db()
    conn = db.connect()
    try:
        if not seed_data.is_seeded(conn):
            seed_data.seed_demo(conn)
            print("Demo data created (25 subjects, 300 students, 8 rooms, 10 slots).")
    finally:
        conn.close()


bootstrap()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
