from __future__ import annotations

from smlib.dot import Graph


def _walk(g: Graph) -> list[str]:
    start = g.initial()
    if start not in g.nodes:
        return []
    order, queue = [start], [start]
    while queue:
        for e in g.exits(queue.pop(0)):
            if e["dst"] in g.nodes and e["dst"] not in order:
                order.append(e["dst"])
                queue.append(e["dst"])
    return order


def outline(g: Graph, current: str | None = None) -> str:
    reached = [s for s in _walk(g) if s in g.states()]
    rest = [s for s in g.states() if s not in reached]
    ids = max([len(s) for s in g.states()] + [len(e["dst"]) for e in g.edges] + [0])
    labels = max([len(g.label(s)) for s in g.states()] + [0])
    listed: set[str] = set()
    lines = [g.name]

    def block(state: str) -> None:
        listed.add(state)
        glyph = "●" if state == current else "◎" if g.is_final(state) else "○"
        line = f"  {glyph} {state:<{ids}}  {g.label(state):<{labels}}"
        if state == current:
            line += "  ◀ current"
        lines.append(line.rstrip())
        exits = g.exits(state)
        for i, e in enumerate(exits):
            tree = "└" if i == len(exits) - 1 else "├"
            arrow = "↩" if e["dst"] in listed else "▶"
            line = f"      {tree}─{arrow} {e['dst']:<{ids}}"
            if e["label"]:
                line += f"  {e['label']}"
            if e["check"]:
                line += f"  ⚙ {e['check']}"
            lines.append(line.rstrip())

    for s in reached:
        block(s)
    if rest:
        lines.append("unreachable:")
        for s in rest:
            block(s)
    return "\n".join(lines)


def highlighted(dot_text: str, current: str | None) -> str:
    if current is None:
        return dot_text
    i = dot_text.rindex("}")
    return f'{dot_text[:i]}{current} [style=filled, fillcolor="#ffd54f"];\n{dot_text[i:]}'


def spaced(dot_text: str) -> str:
    # wider nodesep keeps the two arrows of a cycle apart; placed first so the file's own value wins
    i = dot_text.index("{") + 1
    return f"{dot_text[:i]} nodesep=1.2;{dot_text[i:]}"
