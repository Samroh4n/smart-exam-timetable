"""Timetable validation, kept independent of the algorithms and scheduler."""
from collections import defaultdict

from database import query_all, query_one


def coloring_is_valid(graph, coloring):
    """True when every node is colored and no edge joins equal colors."""
    if set(coloring) != set(graph.nodes):
        return False
    return all(coloring[u] != coloring[v] for u, v in graph.edges)


def _check(name, problems):
    return {"name": name, "passed": not problems, "details": problems[:15],
            "extra": max(0, len(problems) - 15)}


def validate_run(conn, run_id, graph):
    """Run the six required checks on one saved timetable run."""
    entries = query_all(conn, "SELECT * FROM timetable_entries WHERE run_id = ?", (run_id,))
    subjects = query_all(conn, "SELECT subject_id, subject_code FROM subjects")
    code = {s["subject_id"]: s["subject_code"] for s in subjects}
    slot_ids = {r["slot_id"] for r in query_all(conn, "SELECT slot_id FROM time_slots")}
    rooms = {r["room_id"]: r["capacity"] for r in query_all(conn, "SELECT room_id, capacity FROM rooms")}
    enroll = {r["subject_id"]: r["n"] for r in query_all(
        conn, "SELECT subject_id, COUNT(*) AS n FROM registrations GROUP BY subject_id")}

    by_subject = defaultdict(list)
    for e in entries:
        by_subject[e["subject_id"]].append(e)

    # 1. exactly one slot per subject
    p1 = []
    for sid in code:
        count = len(by_subject.get(sid, []))
        if count != 1:
            p1.append(f"{code[sid]} has {count} assigned slots (expected exactly 1)")
    for sid in by_subject:
        if sid not in code:
            p1.append(f"Entry refers to unknown subject {sid}")

    # 2. conflicting subjects never share a slot
    p2 = []
    for u, v in graph.edges:
        eu, ev = by_subject.get(u), by_subject.get(v)
        if eu and ev and eu[0]["slot_id"] == ev[0]["slot_id"]:
            p2.append(f"{code.get(u, u)} and {code.get(v, v)} conflict but share slot {eu[0]['slot_id']}")

    # 3. every subject has a room
    p3 = []
    for sid in code:
        e = by_subject.get(sid)
        if not e:
            p3.append(f"{code[sid]} has no timetable entry, so no room")
        elif not e[0]["room_id"]:
            p3.append(f"{code[sid]} has no room assigned")
        elif e[0]["room_id"] not in rooms:
            p3.append(f"{code[sid]} uses room {e[0]['room_id']} which no longer exists")

    # 4. capacity is enough
    p4 = []
    for sid in code:
        e = by_subject.get(sid)
        if e and e[0]["room_id"] in rooms and enroll.get(sid, 0) > rooms[e[0]["room_id"]]:
            p4.append(f"{code[sid]}: {enroll.get(sid, 0)} students exceed capacity "
                      f"{rooms[e[0]['room_id']]} of room {e[0]['room_id']}")

    # 5. no room double-booked in a slot
    used = defaultdict(list)
    for e in entries:
        if e["room_id"]:
            used[(e["slot_id"], e["room_id"])].append(code.get(e["subject_id"], e["subject_id"]))
    p5 = [f"Room {r} in slot {s} is booked for: {', '.join(subs)}"
          for (s, r), subs in used.items() if len(subs) > 1]

    # 6. assigned slots exist
    p6 = [f"{code.get(e['subject_id'], e['subject_id'])} uses missing slot {e['slot_id']}"
          for e in entries if e["slot_id"] not in slot_ids]

    checks = [
        _check("Every subject has exactly one assigned time slot", p1),
        _check("Conflicting subjects never share a time slot", p2),
        _check("Every subject has an assigned room", p3),
        _check("Room capacity is enough for subject enrollment", p4),
        _check("No room is double-booked in one slot", p5),
        _check("All assigned slots exist", p6),
    ]
    return {"checks": checks, "all_passed": all(c["passed"] for c in checks)}
