from __future__ import annotations

from smlib.dot import Graph
from smlib.store import HOOKS


def lint(g: Graph, label_max: int = 40) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    entries = g.entries()
    if len(entries) != 1:
        errors.append(f"entry: expected exactly one entry node (shape=point), found {len(entries)}")
    for ent in entries:
        outs = g.exits(ent)
        if any(e["dst"] == ent for e in g.edges):
            errors.append(f"entry {ent} has an in-edge")
        if len(outs) != 1:
            errors.append(f"entry {ent} must have exactly one out-edge, found {len(outs)}")
        for e in outs:
            if e["check"]:
                errors.append(f"entry edge {ent} -> {e['dst']} (line {e['line']}) must not have a check")
            if e["ask_set"]:
                errors.append(f"entry edge {ent} -> {e['dst']} (line {e['line']}) must not have an ask")

    for k in g.attrs:
        if k.startswith("on_") and k not in HOOKS:
            errors.append(f"graph attribute {k}: unknown hook; hooks: {', '.join(HOOKS)}")

    seen: set[tuple[str, str]] = set()
    for e in g.edges:
        pair = (e["src"], e["dst"])
        for end in pair:
            if end not in g.nodes:
                errors.append(f"edge {e['src']} -> {e['dst']} (line {e['line']}): undeclared state {end}")
        if e["ask_set"] and not e["ask"] and e["src"] not in entries:
            errors.append(f"edge {e['src']} -> {e['dst']} (line {e['line']}): ask needs a question")
        if pair in seen:
            errors.append(f"edge {e['src']} -> {e['dst']} (line {e['line']}): duplicate edge")
        seen.add(pair)

    for s in g.states():
        exits = g.exits(s)
        if not g.nodes[s].get("label"):
            errors.append(f"state {s}: no label")
        if g.is_final(s):
            if exits:
                errors.append(f"state {s}: final state with an exit")
        elif not exits:
            errors.append(f"state {s}: dead end, not final and no exit")
        if len(exits) >= 2:
            for e in exits:
                if not e["label"]:
                    errors.append(f"edge {s} -> {e['dst']} (line {e['line']}): no condition label; {s} has {len(exits)} exits")

    notes: list[str] = []
    for s in g.states():
        if len(g.label(s)) > label_max:
            notes.append(f"state {s}: label is {len(g.label(s))} chars; keep it short, put detail in do=")
    start = g.initial()
    if start is not None:
        reach, todo = {start}, [start]
        while todo:
            for e in g.exits(todo.pop()):
                if e["dst"] not in reach:
                    reach.add(e["dst"])
                    todo.append(e["dst"])
        errors.extend(f"state {s}: unreachable from {start}" for s in g.states() if s not in reach)
        if not any(g.is_final(s) for s in reach if s in g.nodes):
            notes.append("no final state: runs until stopped")
    return errors, notes
