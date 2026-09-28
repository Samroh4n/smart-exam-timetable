"""Demo data: 25 subjects, 300 anonymous students, 8 rooms, 10 time slots.

Students are grouped into five programs of five subjects each. A student takes
3-5 random subjects from their own program, and about one student in five swaps
one of them for a subject from the neighbouring program (an elective). This
gives a realistic, sparse-enough conflict graph that fits in 10 slots.
No real names, e-mails, phone numbers or private data are generated.
"""
import random
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from database import refresh_enrollment, scalar

PROGRAMS = [
    [("CS101", "Programming Fundamentals"), ("CS102", "Data Structures"),
     ("CS201", "Algorithms"), ("CS202", "Database Systems"), ("CS301", "Operating Systems")],
    [("MA101", "Calculus I"), ("MA102", "Linear Algebra"), ("MA201", "Probability"),
     ("MA202", "Statistics"), ("MA301", "Discrete Mathematics")],
    [("EN101", "Circuit Analysis"), ("EN102", "Digital Logic"), ("EN201", "Signals and Systems"),
     ("EN202", "Control Systems"), ("EN301", "Embedded Systems")],
    [("BU101", "Principles of Management"), ("BU102", "Financial Accounting"), ("BU201", "Marketing"),
     ("BU202", "Business Analytics"), ("BU301", "Operations Management")],
    [("SC101", "Physics I"), ("SC102", "Chemistry I"), ("SC201", "Environmental Science"),
     ("SC202", "Biology I"), ("SC301", "Scientific Writing")],
]
ROOMS = [("R01", "Main Hall A", 120), ("R02", "Main Hall B", 100), ("R03", "Room 101", 80),
         ("R04", "Room 102", 80), ("R05", "Room 201", 60), ("R06", "Room 202", 60),
         ("R07", "Room 301", 50), ("R08", "Room 302", 40)]
DEMO_ADMIN = ("admin", "admin123")
DEMO_STUDENT = ("ST001", "student123")


def is_seeded(conn):
    return (scalar(conn, "SELECT COUNT(*) FROM users") or 0) > 0


def _next_monday():
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def seed_demo(conn):
    """Wipe every table and rebuild the demo dataset (deterministic)."""
    rng = random.Random(2024)
    for table in ("timetable_entries", "timetable_runs", "registrations", "students",
                  "subjects", "rooms", "time_slots", "users"):
        conn.execute(f"DELETE FROM {table}")

    # Users first so admin keeps user_id 1 and stays logged in after a reset.
    conn.execute("INSERT INTO users(username, password_hash, role, student_id) VALUES(?,?,?,?)",
                 (DEMO_ADMIN[0], generate_password_hash(DEMO_ADMIN[1]), "admin", None))
    conn.execute("INSERT INTO users(username, password_hash, role, student_id) VALUES(?,?,?,?)",
                 (DEMO_STUDENT[0], generate_password_hash(DEMO_STUDENT[1]), "student", DEMO_STUDENT[0]))

    program_ids = []
    n = 0
    for program in PROGRAMS:
        ids = []
        for code, name in program:
            n += 1
            sid = f"S{n:02d}"
            duration = 180 if code.endswith("301") else 120 if code[2:].startswith("2") else 90
            conn.execute("INSERT INTO subjects VALUES(?,?,?,?,0)", (sid, code, name, duration))
            ids.append(sid)
        program_ids.append(ids)

    for rid, name, cap in ROOMS:
        conn.execute("INSERT INTO rooms VALUES(?,?,?)", (rid, name, cap))

    monday = _next_monday()
    slot_no = 0
    for day in range(5):
        for start, end in (("09:00", "12:00"), ("14:00", "17:00")):
            slot_no += 1
            conn.execute("INSERT INTO time_slots VALUES(?,?,?,?)",
                         (f"T{slot_no:02d}", (monday + timedelta(days=day)).isoformat(), start, end))

    enrolled = set()
    for i in range(1, 301):
        student_id = f"ST{i:03d}"
        conn.execute("INSERT INTO students VALUES(?,?)", (student_id, f"Student {student_id}"))
        p = rng.randrange(len(PROGRAMS))
        k = rng.randint(3, 5)
        if rng.random() < 0.2:
            chosen = rng.sample(program_ids[p], k - 1) + [rng.choice(program_ids[(p + 1) % len(PROGRAMS)])]
        else:
            chosen = rng.sample(program_ids[p], k)
        for sid in chosen:
            conn.execute("INSERT INTO registrations(student_id, subject_id) VALUES(?,?)", (student_id, sid))
            enrolled.add(sid)

    for ids in program_ids:  # guarantee every subject has students
        for sid in ids:
            if sid not in enrolled:
                conn.execute("INSERT INTO registrations(student_id, subject_id) VALUES(?,?)", ("ST001", sid))
    refresh_enrollment(conn)
    conn.commit()
