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


def _gear(e: dict, full: bool) -> str:
    return f"⚙ {e['check']}" if full else "⚙"


def outline(g: Graph, current: str | None = None, full: bool = False) -> str:
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
        if full and g.detail(state):
            lines.append(f"      · {g.detail(state)}")
        exits = g.exits(state)
        for i, e in enumerate(exits):
            tree = "└" if i == len(exits) - 1 else "├"
            arrow = "↩" if e["dst"] in listed else "▶"
            line = f"      {tree}─{arrow} {e['dst']:<{ids}}"
            if e["label"]:
                line += f"  {e['label']}"
            if e["check"]:
                line += f"  {_gear(e, full)}"
            lines.append(line.rstrip())

    for s in reached:
        block(s)
    if rest:
        lines.append("unreachable:")
        for s in rest:
            block(s)
    return "\n".join(lines)


def pseudocode(g: Graph, current: str | None = None, full: bool = False) -> str:
    states = g.states()
    ids = max([len(s) for s in states] + [0])
    succ = {s: [e["dst"] for e in g.exits(s) if e["dst"] in states] for s in states}
    start = g.initial()
    post: list[str] = []
    seen: set[str] = set()

    def visit(s: str) -> None:
        seen.add(s)
        for d in reversed(succ[s]):
            if d not in seen:
                visit(d)
        post.append(s)

    if start in states:
        visit(start)
    rpo = post[::-1]
    rank = {s: i for i, s in enumerate(rpo)}
    preds: dict[str, list[str]] = {s: [] for s in rpo}
    for s in rpo:
        for d in succ[s]:
            if s not in preds[d]:
                preds[d].append(s)

    idom = {start: start} if rpo else {}

    def meet(a: str, b: str) -> str:
        while a != b:
            while rank[a] > rank[b]:
                a = idom[a]
            while rank[b] > rank[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for s in rpo[1:]:
            known = [p for p in preds[s] if p in idom]
            new = known[0]
            for p in known[1:]:
                new = meet(p, new)
            if idom.get(s) != new:
                idom[s], changed = new, True

    def dominates(h: str, u: str) -> bool:
        while u != h and idom[u] != u:
            u = idom[u]
        return u == h

    back = {(u, h) for u in rpo for h in succ[u] if dominates(h, u)}
    body: dict[str, set[str]] = {}
    for u, h in back:
        loop = body.setdefault(h, {h})
        todo = [u]
        while todo:
            n = todo.pop()
            if n not in loop:
                loop.add(n)
                todo.extend(preds[n])

    def loops_of(s: str) -> list[str]:
        return sorted((h for h in body if s in body[h]), key=lambda h: -len(body[h]))

    hoisted: dict[str, list[str]] = {h: [] for h in body}
    inline: dict[str, str] = {}
    merge: dict[str, list[str]] = {s: [] for s in rpo}
    for y in rpo[1:]:
        x = idom[y]
        outside = [h for h in loops_of(x) if y not in body[h]]
        if outside:
            hoisted[outside[0]].append(y)
        elif sum(1 for p in preds[y] if (p, y) not in back) == 1:
            inline[y] = x
        else:
            merge[x].append(y)

    lines = [g.name]

    def state_line(s: str, indent: int) -> str:
        line = f"{' ' * indent}{'@' + s + ':':<{ids + 2}}"
        if g.label(s):
            line += f'  "{g.label(s)}"'
        if g.is_final(s):
            line += "  END"
        if s == current:
            line += "  ◀ current"
        return line.rstrip()

    def put_state(s: str, indent: int) -> None:
        lines.append(state_line(s, indent))
        if full and g.detail(s):
            lines.append(f"{' ' * (indent + 4)}· {g.detail(s)}")

    def prefix(e: dict, many: bool) -> str:
        if not (e["label"] or e["check"] or many):
            return ""
        if not e["label"] and e["check"]:
            return f"IF {_gear(e, full)}"
        return f"IF ({e['label'] or 'no condition'})" + (f"  {_gear(e, full)}" if e["check"] else "")

    def seq(x: str) -> list[tuple[str, bool]]:
        if x in body:
            return [(x, True)] + [e for y in hoisted[x] for e in seq(y)]
        return [(x, False)] + tail(x)

    def tail(x: str) -> list[tuple[str, bool]]:
        ex = g.exits(x)
        chain = seq(ex[0]["dst"]) if len(ex) == 1 and inline.get(ex[0]["dst"]) == x else []
        return chain + [e for m in merge[x] for e in seq(m)]

    def emit(entries: list[tuple[str, bool]], indent: int, top: bool) -> None:
        for i, (x, is_loop) in enumerate(entries):
            if is_loop:
                lines.append(f"{' ' * indent}LOOP")
                emit([(x, False)] + tail(x), indent + 2, True)
            else:
                nxt = entries[i + 1][0] if i + 1 < len(entries) else None
                block(x, indent, nxt, top and i == len(entries) - 1)

    def block(x: str, indent: int, nxt: str | None, last: bool) -> None:
        put_state(x, indent)
        ex = g.exits(x)
        loops = loops_of(x)
        inner = loops[-1] if loops else None
        nested: set[str] = set()
        pad = " " * (indent + 2)
        for e in ex:
            y, pre = e["dst"], prefix(e, len(ex) > 1)
            if not pre and (y == nxt or (y == inner and last)):
                continue
            if len(ex) > 1 and inline.get(y) == x and y not in nested:
                nested.add(y)
                lines.append(f"{pad}{pre}")
                emit(seq(y), indent + 4, False)
                continue
            if y == inner:
                to = "CONTINUE"
            elif inner and hoisted[inner] and y == hoisted[inner][0]:
                to = "BREAK"
            else:
                to = f"GOTO @{y}"
            lines.append(f"{pad}{pre}  {to}" if pre else f"{pad}{to}")

    if rpo:
        emit(seq(start), 2, False)
    rest = [s for s in states if s not in rank]
    if rest:
        lines.append("-- unreachable --")
        for s in rest:
            put_state(s, 2)
            ex = g.exits(s)
            for e in ex:
                pre = prefix(e, len(ex) > 1)
                lines.append(f"    {pre}  GOTO @{e['dst']}" if pre else f"    GOTO @{e['dst']}")
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
