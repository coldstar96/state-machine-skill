from __future__ import annotations

import re

_ID = re.compile(r"(?:[A-Za-z0-9_.]|-(?!>))+")
_STATE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class Graph:
    def __init__(self, name: str) -> None:
        self.name = name
        self.nodes: dict[str, dict[str, str]] = {}
        self.edges: list[dict] = []

    def entries(self) -> list[str]:
        return [n for n, a in self.nodes.items() if a.get("shape") == "point"]

    def states(self) -> list[str]:
        entries = self.entries()
        return [n for n in self.nodes if n not in entries]

    def initial(self) -> str | None:
        entries = self.entries()
        if not entries:
            return None
        for e in self.edges:
            if e["src"] == entries[0]:
                return e["dst"]
        return None

    def label(self, state: str) -> str:
        return self.nodes.get(state, {}).get("label", "").replace("\\n", " ")

    def detail(self, state: str) -> str:
        return self.nodes.get(state, {}).get("do", "").replace("\\n", " ")

    def is_final(self, state: str) -> bool:
        return self.nodes.get(state, {}).get("shape") == "doublecircle"

    def exits(self, state: str) -> list[dict]:
        return [e for e in self.edges if e["src"] == state]


def _tokenize(text: str) -> list[tuple[str, str, int]]:
    toks: list[tuple[str, str, int]] = []
    i, n, line, fresh = 0, len(text), 1, True

    def err(why: str):
        toks.append(("ERR", why, line))
        return toks

    while i < n:
        c = text[i]
        if c == "\n":
            line, fresh, i = line + 1, True, i + 1
            continue
        if c in " \t\r":
            i += 1
            continue
        starts_line, fresh = fresh, False
        if text.startswith("//", i) or (c == "#" and starts_line):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                return err("unterminated comment")
            line += text.count("\n", i, j)
            i = j + 2
        elif c == '"':
            j, buf = i + 1, []
            while True:
                if j >= n:
                    return err("unterminated quoted string")
                ch = text[j]
                if ch == "\n":
                    return err("quoted string may not span lines")
                if ch == "\\" and j + 1 < n and text[j + 1] in '"\\':
                    buf.append(text[j + 1])
                    j += 2
                elif ch == '"':
                    break
                else:
                    buf.append(ch)
                    j += 1
            toks.append(("STR", "".join(buf), line))
            i = j + 1
        elif text.startswith("->", i):
            toks.append(("->", "->", line))
            i += 2
        elif text.startswith("--", i):
            return err("undirected edges '--' are not supported")
        elif c in "[]=,;{}":
            toks.append((c, c, line))
            i += 1
        elif c == ":":
            return err("ports are not supported")
        elif c == "<":
            return err("HTML labels are not supported")
        else:
            m = _ID.match(text, i)
            if not m:
                return err(f"unexpected character {c!r}")
            toks.append(("ID", m.group(), line))
            i = m.end()
    toks.append(("EOF", "", line))
    return toks


def parse(text: str) -> Graph:
    toks = _tokenize(text)
    toks.reverse()

    def fail(t, why: str):
        raise ValueError(f"line {t[2]}: {why}")

    def peek():
        t = toks[-1]
        if t[0] == "ERR":
            fail(t, t[1])
        return t

    def take(*kinds: str):
        t = peek()
        if kinds and t[0] not in kinds:
            fail(t, f"expected {' or '.join(kinds)}, found {t[1]!r}" if t[0] != "EOF" else "unexpected end of file")
        if t[0] != "EOF":
            toks.pop()
        return t

    def state_id(t) -> str:
        if t[0] == "ID" and _STATE.match(t[1]):
            return t[1]
        fail(t, f"bad state id {t[1]!r}: use letters, digits and underscores")

    def attrs() -> dict[str, str]:
        take("[")
        out: dict[str, str] = {}
        while peek()[0] != "]":
            if peek()[0] in (",", ";"):
                take()
                continue
            key = take("ID", "STR")[1]
            take("=")
            out[key] = take("ID", "STR")[1]
        take("]")
        return out

    head = take()
    if head[0] == "ID" and head[1] == "graph":
        fail(head, "undirected graphs are not supported; use digraph")
    if head[0] != "ID" or head[1] != "digraph":
        fail(head, "expected 'digraph'")
    name = take()[1] if peek()[0] in ("ID", "STR") else ""
    take("{")
    g = Graph(name)

    while True:
        t = peek()
        if t[0] == ";":
            take()
            continue
        if t[0] == "}":
            take()
            break
        if t[0] == "{":
            fail(t, "subgraphs are not supported")
        if t[0] not in ("ID", "STR"):
            fail(t, "unexpected end of file" if t[0] == "EOF" else f"unexpected {t[1]!r}")
        first = take()
        if first[0] == "ID" and first[1] == "subgraph":
            fail(first, "subgraphs are not supported")
        if first[0] == "ID" and first[1] in ("node", "edge", "graph") and peek()[0] == "[":
            if "check" in attrs():
                fail(first, "check is not supported as a default; put it on each edge")
            continue
        if peek()[0] == "=":
            take()
            take("ID", "STR")
            continue
        src = state_id(first)
        if peek()[0] == "->":
            take()
            dst = state_id(take("ID", "STR"))
            if peek()[0] == "->":
                fail(peek(), "chained edges are not supported")
            a = attrs() if peek()[0] == "[" else {}
            g.edges.append({"src": src, "dst": dst, "label": a.get("label", ""),
                            "check": a.get("check", ""), "line": first[2]})
        else:
            g.nodes.setdefault(src, {}).update(attrs() if peek()[0] == "[" else {})

    if peek()[0] != "EOF":
        fail(peek(), "unexpected text after '}'")
    return g
