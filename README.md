# Smart Exam Timetable Scheduling

A Flask website that builds a **conflict-free exam timetable** using graph coloring.

- **Node** = a subject (exam)
- **Edge** = at least one student takes both subjects (they cannot share a slot)
- **Color** = one exam time slot

## Features

- Admin and student roles with Flask-Login and hashed passwords (Werkzeug)
- Admin: dashboard, add/edit/delete for students, subjects, registrations, rooms and time slots, CSV upload for each, demo-data reset
- Five real, separate coloring algorithms run on the same NetworkX conflict graph
- Timetable generation with room allocation by capacity
- Algorithm comparison table, bar charts (Chart.js) and a recommendation calculated from the current data
- Validation report with six pass/fail checks
- Excel (openpyxl) and PDF (ReportLab) exports; students can download only their own PDF
- Interactive conflict-graph view in the browser

## The five algorithms (`algorithms.py`)

| Algorithm | Idea | Complexity | Limitation |
|---|---|---|---|
| Greedy | Visit subjects in id order, give the smallest valid color | O(V + E) | Result depends on order |
| Welsh-Powell | Sort by degree; fill one color at a time with non-adjacent subjects | O(V^2) | Static ordering |
| Largest Degree First | Repeatedly take the uncolored subject with most uncolored neighbors | O(V^2 + V·E) | Ignores neighbor colors |
| DSATUR | Repeatedly take the subject whose neighbors use the most distinct colors (ties: degree) | O(V^2) | Heuristic, not guaranteed optimal |
| Backtracking | Branch and bound search for the minimum number of colors, with a time limit | Exponential | Slow on large or dense graphs |

Every function returns `{"status", "coloring", "slots_used", "runtime_seconds"}`. If Backtracking hits its time limit the status is `timed_out` and the site shows **Timed out** instead of any slot count. A completed Backtracking result is a proven minimum for that graph; no other algorithm is called optimal.

## Demo dataset

25 subjects (S01-S25, five programs of five subjects), 300 anonymous students (ST001-ST300, named "Student ST001" and so on), 8 rooms (40-120 seats) and 10 time slots (5 days, 2 per day). Each student takes 3-5 random subjects, mostly from one program with an occasional elective from the next. Every subject has enrolled students. No real names, e-mails or phone numbers are used.

## Demo logins (local testing only)

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin123` |
| Student | `ST001` | `student123` |

Only these two demo accounts exist at first. Admins can create logins for other students under Data > Students. Change these passwords before any real use.

## Setup on Windows

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open: http://127.0.0.1:5000

Optional: copy `.env.example` to `.env` and set `FLASK_SECRET_KEY` to a long random value. Without it a temporary key is generated on each start. The comparison charts load Chart.js from a CDN, so they need an internet connection; tables work offline. The SQLite file `smart_exam.db` and the `exports/` folder are created automatically.

## CSV formats

Header rows (any column order, lower-case names):

- students: `student_id,student_name`
- subjects: `subject_id,subject_code,subject_name,duration_minutes`
- registrations: `student_id,subject_id`
- rooms: `room_id,room_name,capacity`
- slots: `slot_id,exam_date,start_time,end_time` (dates `YYYY-MM-DD`, times `HH:MM`)

Templates can be downloaded from each Data tab. Invalid rows are skipped and reported.

## Project layout

`app.py` routes and exports, `database.py` SQLite, `algorithms.py` the five algorithms, `scheduler.py` graph, slot and room assignment, `validators.py` checks, `seed_data.py` demo data.

## Submit to GitHub

```
git init
git branch -M main
git add .
git commit -m "Initial smart exam timetable scheduling website"
```

Create an empty GitHub repository named `smart-exam-timetable` (no README, no .gitignore), then run:

```
git remote add origin https://github.com/YOUR-USERNAME/smart-exam-timetable.git
git push -u origin main
```
