"""Graph-coloring algorithms for exam timetabling.

Model: node = subject, edge = at least one student takes both subjects,
color = exam time slot. Every function receives the same NetworkX graph and
returns a dict:

    {"status": "completed" | "timed_out",
     "coloring": {"S01": 0, ...},
     "slots_used": int (None when timed out),
     "runtime_seconds": float}
"""
import sys
import time


def _finish(status, coloring, start):
    runtime = time.perf_counter() - start
    if status == "timed_out":
        # A timed-out search proves nothing, so no coloring is reported.
        return {"status": status, "coloring": {}, "slots_used": None,
                "runtime_seconds": runtime}
    return {"status": status, "coloring": coloring,
            "slots_used": len(set(coloring.values())),
            "runtime_seconds": runtime}


def _smallest_valid_color(graph, node, coloring):
    """Smallest non-negative color not used by an already colored neighbor."""
    used = {coloring[n] for n in graph[node] if n in coloring}
    color = 0
    while color in used:
        color += 1
    return color


# ---------------------------------------------------------------------------
# 1. Greedy Coloring
# Logic: visit subjects in a fixed (sorted-by-id) order and give each the
#        smallest color not used by an already colored neighbor.
# Complexity: O(V + E) (plus sorting, O(V log V)).
# Advantage: extremely fast and simple.
# Limitation: result depends on visiting order; can use many more colors
#             than necessary because the order ignores graph structure.
# ---------------------------------------------------------------------------
def greedy_coloring(graph):
    start = time.perf_counter()
    coloring = {}
    for node in sorted(graph.nodes, key=str):
        coloring[node] = _smallest_valid_color(graph, node, coloring)
    return _finish("completed", coloring, start)


# ---------------------------------------------------------------------------
# 2. Welsh-Powell Coloring
# Logic: sort subjects by descending degree. Take color 0 and walk down the
#        sorted list giving it to every uncolored subject that is not adjacent
#        to a subject already holding that color; then repeat with color 1,
#        2, ... until everything is colored (one color class at a time).
# Complexity: O(V^2) in this implementation (O(V log V) sort + class building).
# Advantage: high-degree (hardest) subjects are placed first; usually good.
# Limitation: uses static degrees and is not guaranteed to be optimal.
# ---------------------------------------------------------------------------
def welsh_powell_coloring(graph):
    start = time.perf_counter()
    order = sorted(graph.nodes, key=lambda n: (-graph.degree(n), str(n)))
    coloring = {}
    color = 0
    remaining = list(order)
    while remaining:
        members = []
        for node in remaining:
            if all(not graph.has_edge(node, m) for m in members):
                members.append(node)
        for node in members:
            coloring[node] = color
        chosen = set(members)
        remaining = [n for n in remaining if n not in chosen]
        color += 1
    return _finish("completed", coloring, start)


# ---------------------------------------------------------------------------
# 3. Largest Degree First Coloring
# Logic: repeatedly pick the uncolored subject with the most UNCOLORED
#        neighbors (residual degree; ties broken by full degree, then id) and
#        give it the smallest valid color. The priority is recomputed after
#        every assignment, unlike Welsh-Powell's fixed ordering.
# Complexity: O(V^2 + V*E) in this implementation (residual degree recount).
# Advantage: adapts to the shrinking problem; often better than plain greedy.
# Limitation: ignores which colors neighbors already use (DSATUR does not),
#             so it can still exceed the optimum.
# ---------------------------------------------------------------------------
def largest_degree_first_coloring(graph):
    start = time.perf_counter()
    coloring = {}
    uncolored = set(graph.nodes)
    while uncolored:
        node = min(uncolored, key=lambda n: (
            -sum(1 for m in graph[n] if m in uncolored),
            -graph.degree(n), str(n)))
        coloring[node] = _smallest_valid_color(graph, node, coloring)
        uncolored.remove(node)
    return _finish("completed", coloring, start)


# ---------------------------------------------------------------------------
# 4. DSATUR Coloring
# Logic: repeatedly choose the uncolored subject with the greatest saturation
#        degree (number of distinct colors among its neighbors); break ties
#        with ordinary degree, then id. Give it the smallest valid color.
# Complexity: O(V^2) with the simple selection used here.
# Advantage: focuses on the most constrained subjects; typically the fewest
#            slots among fast heuristics; exact on bipartite/cycle graphs.
# Limitation: still a heuristic, so optimality is not guaranteed.
# ---------------------------------------------------------------------------
def dsatur_coloring(graph):
    start = time.perf_counter()
    coloring = {}
    uncolored = set(graph.nodes)
    while uncolored:
        node = min(uncolored, key=lambda n: (
            -len({coloring[m] for m in graph[n] if m in coloring}),
            -graph.degree(n), str(n)))
        coloring[node] = _smallest_valid_color(graph, node, coloring)
        uncolored.remove(node)
    return _finish("completed", coloring, start)


def _greedy_clique_size(graph, order):
    """Size of a clique found greedily; a lower bound on the colors needed."""
    best = 0
    for seed in order:
        clique = [seed]
        for cand in order:
            if cand != seed and all(graph.has_edge(cand, m) for m in clique):
                clique.append(cand)
        best = max(best, len(clique))
    return best


# ---------------------------------------------------------------------------
# 5. Backtracking Coloring (branch and bound)
# Logic: assign subjects in descending-degree order, recursively trying each
#        valid color (a brand-new color is only tried after all used colors,
#        which removes symmetric duplicates). Every complete coloring found
#        tightens the bound, so later branches must use fewer colors. The
#        search ends when it exhausts all possibilities or reaches the clique
#        lower bound; in both cases the answer is the proven minimum. If the
#        time limit expires first, the result is reported as "timed_out".
# Complexity: exponential in the worst case, O(k^V).
# Advantage: can prove the minimum number of slots on small/sparse graphs.
# Limitation: becomes very slow on large or dense graphs.
# ---------------------------------------------------------------------------
def backtracking_coloring(graph, time_limit=10):
    start = time.perf_counter()
    try:
        limit = max(0.0, float(time_limit))
    except (TypeError, ValueError):
        limit = 10.0
    deadline = start + limit
    nodes = sorted(graph.nodes, key=lambda n: (-graph.degree(n), str(n)))
    n = len(nodes)
    if n == 0:
        return _finish("completed", {}, start)

    sys.setrecursionlimit(max(sys.getrecursionlimit(), n + 500))
    lower_bound = _greedy_clique_size(graph, nodes)
    state = {"best_k": n + 1, "best": None, "timed_out": False, "done": False}
    coloring = {}

    def solve(i, used):
        if time.perf_counter() > deadline:
            state["timed_out"] = True
            return
        if i == n:
            state["best_k"] = used
            state["best"] = dict(coloring)
            if used <= lower_bound:
                state["done"] = True  # cannot do better than the clique bound
            return
        node = nodes[i]
        forbidden = {coloring[m] for m in graph[node] if m in coloring}
        # Only colors that can still beat the best solution found so far.
        for color in range(min(used + 1, state["best_k"] - 1)):
            if color in forbidden:
                continue
            coloring[node] = color
            solve(i + 1, max(used, color + 1))
            del coloring[node]
            if state["timed_out"] or state["done"]:
                return

    solve(0, 0)
    if state["timed_out"]:
        return _finish("timed_out", {}, start)
    return _finish("completed", state["best"], start)
