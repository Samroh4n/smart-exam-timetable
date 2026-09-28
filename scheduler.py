"""Scheduling logic: conflict graph, algorithm dispatch, slot and room assignment."""
from datetime import datetime

import networkx as nx
import pandas as pd

import validators
from algorithms import (backtracking_coloring, dsatur_coloring, greedy_coloring,
                        largest_degree_first_coloring, welsh_powell_coloring)
from database import query_all

ALGORITHMS = {
    "greedy": ("Greedy Coloring", greedy_coloring),
    "welsh_powell": ("Welsh-Powell Coloring", welsh_powell_coloring),
    "ldf": ("Largest Degree First Coloring", largest_degree_first_coloring),
    "dsatur": ("DSATUR Coloring", dsatur_coloring),
    "backtracking": ("Backtracking Coloring", backtracking_coloring),
}


def build_conflict_graph(conn):
    """Nodes are subjects; an edge means at least one student takes both.

    The edge attribute `weight` holds the number of shared students.
    """
    graph = nx.Graph()
    for s in query_all(conn, "SELECT subject_id, subject_code, subject_name, enrollment_count "
                             "FROM subjects ORDER BY subject_id"):
        graph.add_node(s["subject_id"], code=s["subject_code"], name=s["subject_name"],
                       enrollment=s["enrollment_count"])
    regs = pd.read_sql_query(
        "SELECT r.student_id, r.subject_id FROM registrations r "
        "JOIN subjects s ON s.subject_id = r.subject_id", conn).drop_duplicates()
    if not regs.empty:
        pairs = regs.merge(regs, on="student_id")
        pairs = pairs[pairs["subject_id_x"] < pairs["subject_id_y"]]
        shared = pairs.groupby(["subject_id_x", "subject_id_y"]).size().reset_index(name="shared")
        for row in shared.itertuples(index=False):
            graph.add_edge(row.subject_id_x, row.subject_id_y, weight=int(row.shared))
    return graph


def run_algorithm(key, graph, time_limit=10):
    name, func = ALGORITHMS[key]
    if key == "backtracking":
        return func(graph, time_limit=time_limit)
    return func(graph)


def compare_algorithms(graph, time_limit=10):
    """Run all five algorithms on the same graph and pick a recommendation."""
    rows = []
    for key, (name, _) in ALGORITHMS.items():
        result = run_algorithm(key, graph, time_limit)
        completed = result["status"] == "completed"
        rows.append({
            "key": key, "name": name, "status": result["status"],
            "slots_used": result["slots_used"], "runtime": result["runtime_seconds"],
            "valid": completed and validators.coloring_is_valid(graph, result["coloring"]),
        })
    return rows, recommend(rows)


def recommend(rows):
    """Fewest slots among valid completed results; ties go to the faster run."""
    candidates = [r for r in rows if r["valid"] and r["status"] == "completed"]
    if not candidates:
        return None
    best = min(candidates, key=lambda r: (r["slots_used"], r["runtime"]))
    tied = [r for r in candidates if r["slots_used"] == best["slots_used"] and r is not best]
    text = (f"{best['name']} is recommended for the current dataset: it produced a valid coloring "
            f"with {best['slots_used']} slot(s) in {best['runtime'] * 1000:.3f} ms.")
    if tied:
        text += (" It ties on slot count with " + ", ".join(t["name"] for t in tied) +
                 " and was chosen because it ran fastest.")
    else:
        text += " No other valid algorithm used as few slots."
    bt = next((r for r in rows if r["key"] == "backtracking"), None)
    if bt and bt["status"] == "completed" and bt["valid"]:
        if best["slots_used"] == bt["slots_used"]:
            text += (f" Backtracking finished, so {bt['slots_used']} slot(s) is the proven minimum "
                     "for this graph.")
        else:
            text += (f" Backtracking finished with {bt['slots_used']} slot(s), the proven minimum "
                     "for this graph.")
    else:
        text += (" Backtracking did not finish, so no result is proven optimal.")
    return {"name": best["name"], "key": best["key"], "slots_used": best["slots_used"],
            "runtime": best["runtime"], "explanation": text}


def colors_to_slots(coloring, slots):
    """Map color k to the k-th chronological slot. Returns None if too few slots."""
    needed = (max(coloring.values()) + 1) if coloring else 0
    if needed > len(slots):
        return None
    return {color: slots[color] for color in range(needed)}


def assign_rooms(coloring, enrollment, color_to_slot, rooms):
    """Within each slot, give the largest subject the smallest room that fits it.

    Returns {subject_id: room_id or None}. None means no free room is big enough.
    """
    by_slot = {}
    for subject, color in coloring.items():
        by_slot.setdefault(color, []).append(subject)
    result = {}
    rooms_sorted = sorted(rooms, key=lambda r: (r["capacity"], r["room_id"]))
    for color, subjects in by_slot.items():
        free = list(rooms_sorted)
        for subject in sorted(subjects, key=lambda s: (-enrollment.get(s, 0), s)):
            need = enrollment.get(subject, 0)
            chosen = next((r for r in free if r["capacity"] >= need), None)
            if chosen:
                free.remove(chosen)
            result[subject] = chosen["room_id"] if chosen else None
    return result


def generate_timetable(conn, key, time_limit=10):
    """Run one algorithm, save the run and return {ok, message, run_id, result}."""
    graph = build_conflict_graph(conn)
    if graph.number_of_nodes() == 0:
        return {"ok": False, "message": "There are no subjects. Add subjects before generating.", "run_id": None}
    name = ALGORITHMS[key][0]
    result = run_algorithm(key, graph, time_limit)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if result["status"] == "timed_out":
        cur = conn.execute(
            "INSERT INTO timetable_runs(algorithm_name, slots_used, runtime_seconds, valid, status, generated_at) "
            "VALUES(?,?,?,?,?,?)", (name, None, result["runtime_seconds"], 0, "timed_out", now))
        conn.commit()
        return {"ok": False, "run_id": cur.lastrowid, "result": result,
                "message": f"Timed out: {name} did not finish within {time_limit} seconds, so no "
                           "timetable was created. Raise the time limit or choose another algorithm."}

    slots = query_all(conn, "SELECT * FROM time_slots ORDER BY exam_date, start_time, slot_id")
    color_slot = colors_to_slots(result["coloring"], slots)
    if color_slot is None:
        return {"ok": False, "run_id": None, "result": result,
                "message": f"{name} needs {result['slots_used']} slots but only {len(slots)} are "
                           "available. Add time slots or choose an algorithm that uses fewer."}

    rooms = query_all(conn, "SELECT * FROM rooms")
    enroll = {s["subject_id"]: s["enrollment_count"] for s in query_all(
        conn, "SELECT subject_id, enrollment_count FROM subjects")}
    room_of = assign_rooms(result["coloring"], enroll, color_slot, rooms)

    cur = conn.execute(
        "INSERT INTO timetable_runs(algorithm_name, slots_used, runtime_seconds, valid, status, generated_at) "
        "VALUES(?,?,?,?,?,?)", (name, result["slots_used"], result["runtime_seconds"], 0, "completed", now))
    run_id = cur.lastrowid
    for subject, color in result["coloring"].items():
        conn.execute(
            "INSERT INTO timetable_entries(run_id, subject_id, slot_id, room_id, assigned_color) "
            "VALUES(?,?,?,?,?)", (run_id, subject, color_slot[color]["slot_id"], room_of.get(subject), color))
    report = validators.validate_run(conn, run_id, graph)
    conn.execute("UPDATE timetable_runs SET valid = ? WHERE run_id = ?", (1 if report["all_passed"] else 0, run_id))
    conn.commit()
    msg = f"{name} generated a timetable using {result['slots_used']} slot(s)."
    if not report["all_passed"]:
        msg += " Validation found problems; open the validation report for details."
    return {"ok": True, "run_id": run_id, "result": result, "message": msg, "valid": report["all_passed"]}
