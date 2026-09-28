"""SQLite access layer: connection helper, schema and small query helpers."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "smart_exam.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    student_id TEXT
);
CREATE TABLE IF NOT EXISTS students(
    student_id TEXT PRIMARY KEY,
    student_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subjects(
    subject_id TEXT PRIMARY KEY,
    subject_code TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    enrollment_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS registrations(
    registration_id INTEGER PRIMARY KEY,
    student_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    UNIQUE(student_id, subject_id)
);
CREATE TABLE IF NOT EXISTS rooms(
    room_id TEXT PRIMARY KEY,
    room_name TEXT NOT NULL,
    capacity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS time_slots(
    slot_id TEXT PRIMARY KEY,
    exam_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timetable_runs(
    run_id INTEGER PRIMARY KEY,
    algorithm_name TEXT NOT NULL,
    slots_used INTEGER,
    runtime_seconds REAL,
    valid INTEGER,
    status TEXT,
    generated_at TEXT
);
CREATE TABLE IF NOT EXISTS timetable_entries(
    entry_id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    subject_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    room_id TEXT,
    assigned_color INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reg_student ON registrations(student_id);
CREATE INDEX IF NOT EXISTS idx_reg_subject ON registrations(subject_id);
CREATE INDEX IF NOT EXISTS idx_entries_run ON timetable_entries(run_id);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def query_all(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def query_one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def refresh_enrollment(conn):
    conn.execute(
        "UPDATE subjects SET enrollment_count = "
        "(SELECT COUNT(*) FROM registrations r WHERE r.subject_id = subjects.subject_id)")


def latest_completed_run(conn):
    return query_one(
        conn,
        "SELECT * FROM timetable_runs WHERE status = 'completed' "
        "AND EXISTS (SELECT 1 FROM timetable_entries e WHERE e.run_id = timetable_runs.run_id) "
        "ORDER BY run_id DESC LIMIT 1")
