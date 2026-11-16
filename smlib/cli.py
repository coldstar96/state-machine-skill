from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from smlib.dot import Graph, parse
from smlib.lint import lint
from smlib.render import highlighted, outline, pseudocode, spaced
from smlib.store import CONFIG_KEYS, Run, changes, home, load_config, save_config


NAME = r"[a-z][a-z0-9_]*"
RULE = "rule: only this state's work; if it does not fit, stop and report (BLOCKED.md), no jump, no graph edits"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _var_name(name: str) -> str:
    if not re.fullmatch(NAME, name) or name.endswith(("_len", "_proxy")) or name.startswith("npm_config_"):
        raise ValueError(f"bad variable name '{name}': lowercase letters, digits, _; "
                         "not ending in _len or _proxy, not npm_config_*")
    return name


def _vars(state) -> dict:
    v = state.get("vars") if isinstance(state, dict) else None
    return v if isinstance(v, dict) else {}


def _show(v, n: int = 60) -> str:
    if isinstance(v, list):
        return f"[{len(v)} items]"
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(v))
    return s if len(s) <= n else s[:n - 3] + "..."


def _save_var(run: Run, state: dict, **entry) -> None:
    run.save(state)
    run.log(kind="var", step=state["steps"], **entry)


def _read(run: Run) -> str:
    try:
        return run.dot.read_text()
    except FileNotFoundError:
        raise ValueError(f"no machine.dot in {run.dir}")


def _settings() -> dict[str, str]:
    # a broken config.json must not stop reading or drawing a machine
    try:
        return load_config()
    except ValueError:
        return {}


def _label_max() -> int:
    try:
        return int(_settings().get("label_max", 40))
    except ValueError:
        return 40


def _parse_lint(text: str) -> tuple[Graph | None, list[str], list[str]]:
    try:
        g = parse(text)
    except ValueError as e:
        return None, [str(e)], []
    errors, notes = lint(g, _label_max())
    return g, errors, notes


def _load(run: Run) -> tuple[Graph | None, list[str], list[str]]:
    return _parse_lint(_read(run))


def _sync(run: Run, state: dict) -> tuple[Graph | None, list[str], list[str], bool]:
    g, errors, notes = _load(run)
    if errors:
        return g, errors, notes, False
    text = _read(run)
    try:
        applied = run.applied.read_text()
    except FileNotFoundError:
        applied = None
    items: list[str] = []
    if applied != text:
        try:
            items = changes(parse(applied), g) if applied is not None else []
        except ValueError:
            items = []
        if items:
            summary = " · ".join(items)
            print(f"machine changed at step {state['steps']}: {summary}")
            run.log(kind="edit", step=state["steps"], summary=summary)
        run.applied.write_text(text)
    return g, errors, notes, bool(items)


def _invalid(errors: list[str]) -> ValueError:
    return ValueError("machine.dot is invalid:\n" + "\n".join(f"  {e}" for e in errors))


def _open(strict: bool = True) -> tuple[Run, dict, Graph | None, bool]:
    run = Run.active()
    state = run.state()
    g, errors, _, changed = _sync(run, state)
    if errors and strict:
        raise _invalid(errors)
    return run, state, g, changed


def _view(dirarg: str | None) -> tuple[Run, dict | None, Graph | None, list[str], list[str]]:
    run = Run(dirarg) if dirarg else Run.active()
    state = run.state() if run.state_file.exists() else None
    if state is not None and not dirarg:
        g, errors, notes, _ = _sync(run, state)
    else:
        g, errors, notes = _load(run)
    return run, state, g, errors, notes


def _current(g: Graph, state: dict) -> str:
    cur = state["current"]
    if cur not in g.states():
        raise ValueError(f"current state '{cur}' is no longer in the graph; sm jump <state> -m <reason>")
    return cur


def _fill(text: str, vars: dict) -> str:
    def one(m):
        v = vars.get(m.group(1))
        if v is None:
            return m.group(0)
        if isinstance(v, list):
            return ", ".join(_show(i) for i in v[:3]) + (f" (+{len(v) - 3} more)" if len(v) > 3 else "")
        return _show(v)
    return re.sub(r"\{(" + NAME + r")\}", one, text)


def _var_env(state: dict) -> dict[str, str]:
    env = {}
    for k, v in _vars(state).items():
        if isinstance(v, list):
            env[k] = "\n".join(v)
            env[f"{k}_len"] = str(len(v))
        else:
            env[k] = v
    return env


def _where(g: Graph, state: dict) -> str:
    cur = _current(g, state)
    vs = _vars(state)
    head = (f"[{g.name}] {cur} · step {state['steps']} · visit {state['visits'].get(cur, 0)}"
            f" — {_fill(g.label(cur), vs)}")
    if g.detail(cur):
        head += "\ndo: " + _fill(g.detail(cur), vs)
    if vs:
        shown = " · ".join(f"{k}={_show(v)}" for k, v in list(vs.items())[:8])
        head += "\nvars: " + shown + (f" · +{len(vs) - 8} more" if len(vs) > 8 else "")
    if g.is_final(cur):
        return head + "\nfinal state — machine finished"
    return head + "\nexits: " + " | ".join(_exit(e) for e in g.exits(cur)) + "\n" + RULE


def _exit(e: dict) -> str:
    parts = ([e["label"]] if e["label"] else []) + (["checked"] if e["check"] else [])
    return e["dst"] + (f" ({', '.join(parts)})" if parts else "")


def _position(run: Run) -> tuple[str, bool]:
    current = run.state()["current"]
    return current, parse(_read(run)).is_final(current)


def _unfinished(prev: str | None) -> None:
    if not prev:
        return
    try:
        if not _position(Run(prev))[1]:
            print(f"note: this session was on {prev} (not finished)")
    except (OSError, ValueError, KeyError):
        pass


def _check(cmd: str, cwd: str, env: dict[str, str]) -> None:
    if not os.path.isdir(cwd):
        raise ValueError(f"check directory is gone: {cwd}")
    r = subprocess.run(["sh", "-c", cmd], cwd=cwd, env={**os.environ, **env}, stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    if r.returncode:
        tail = r.stdout.splitlines()[-15:]
        raise ValueError("\n".join([f"check failed (exit {r.returncode}): {cmd}", *tail]))


def _notify(run: Run, g: Graph, state: dict) -> None:
    try:
        cmd = load_config().get("on_move")
        if cmd:
            env = {**os.environ, "SM_STATE": state["current"], "SM_MACHINE": g.name, "SM_DIR": str(run.dir),
                   "SM_STEP": str(state["steps"]), **_var_env(state)}
            subprocess.run(cmd, shell=True, cwd=run.dir, env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


def _move(run: Run, state: dict, g: Graph, to: str, reason: str | None, checked: bool, jump: bool) -> int:
    with run.lock():
        fresh = run.state()
        if fresh["current"] != state["current"]:
            raise ValueError("the run moved meanwhile; run sm now")
        fresh["steps"] += 1
        fresh["visits"][to] = fresh["visits"].get(to, 0) + 1
        run.log(kind="move", step=fresh["steps"], to=to, reason=reason, check="ok" if checked else None,
                jump=jump, **{"from": fresh["current"]})
        fresh["current"] = to
        run.save(fresh)
    print(_where(g, fresh))
    _notify(run, g, fresh)
    return 0


def _cmd_validate(args) -> int:
    _, _, g, errors, notes = _view(args.dir)
    if errors:
        print("\n".join(errors), file=sys.stderr)
    else:
        entries = g.entries()
        edges = [e for e in g.edges if e["src"] not in entries]
        print(f"ok: {len(g.states())} states, {len(edges)} edges")
    for n in notes:
        print(f"note: {n}")
    return 1 if errors else 0


def _cmd_show(args) -> int:
    _, state, g, errors, _ = _view(args.dir)
    if errors:
        print(str(_invalid(errors)), file=sys.stderr)
    if g is None:
        return 1
    cur = state["current"] if state else None
    cfg = _settings()
    view, detail = args.view or cfg.get("view"), args.detail or cfg.get("detail")
    print((outline if view == "outline" else pseudocode)(g, cur, detail == "full"))
    return 0


def _cmd_png(args) -> int:
    if not shutil.which("dot"):
        print("Graphviz not found: sm png needs the dot command on PATH", file=sys.stderr)
        return 3
    run, state, _, errors, _ = _view(args.dir)
    if errors:
        raise _invalid(errors)
    out = os.path.abspath(args.output or run.dir / "machine.png")
    text = spaced(highlighted(_read(run), state["current"] if state else None))
    r = subprocess.run(["dot", "-Tpng", "-o", out], input=text, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    if r.returncode:
        raise ValueError(f"dot failed (exit {r.returncode}): {r.stdout.strip()}")
    print(out)
    return 0


def _cmd_init(args) -> int:
    pairs = []
    for s in args.set:
        name, eq, value = s.partition("=")
        if not eq:
            raise ValueError("--set needs name=value")
        pairs.append((_var_name(name), value))
    run = Run(args.dir)
    if run.state_file.exists():
        raise ValueError(f"already started: sm use {run.dir}")
    g, errors, _ = _load(run)
    if errors:
        raise _invalid(errors)
    first = g.initial()
    state = {"current": first, "cwd": os.path.abspath(args.cwd or os.getcwd()), "started": _now(),
             "steps": 0, "visits": {first: 1}}
    if pairs:
        state["vars"] = dict(pairs)
    prev = run.point()
    run.save(state)
    for name, value in pairs:
        run.log(kind="var", step=0, op="set", name=name, value=_show(value, 200))
    run.applied.write_text(_read(run))
    _unfinished(prev)
    print(_where(g, state))
    _notify(run, g, state)
    return 0


def _cmd_use(args) -> int:
    run = Run(args.dir)
    _read(run)
    run.state()
    _unfinished(run.point())
    return _cmd_now(args)


def _cmd_now(args) -> int:
    _, state, g, _ = _open()
    print(_where(g, state))
    return 0


def _cmd_go(args) -> int:
    run, state, g, changed = _open()
    if changed:
        print(_where(g, state))
        return 1
    cur = _current(g, state)
    if g.is_final(cur):
        raise ValueError("machine finished")
    exits = g.exits(cur)
    names = ", ".join(e["dst"] for e in exits)
    target = args.state
    if target is None:
        if len(exits) != 1:
            raise ValueError(f"{cur} has {len(exits)} exits: {names}; sm go <state>")
        target = exits[0]["dst"]
    edge = next((e for e in exits if e["dst"] == target), None)
    if edge is None:
        raise ValueError(f"no edge {cur} -> {target}; exits: {names}")
    if edge["check"]:
        _check(edge["check"], state["cwd"], _var_env(state))
    elif len(exits) > 1 and not args.reason:
        raise ValueError('reason required: -m "<why this exit>"')
    return _move(run, state, g, target, args.reason, bool(edge["check"]), False)


def _cmd_jump(args) -> int:
    run, state, g, _ = _open()
    if args.state not in g.states():
        raise ValueError(f"no such state '{args.state}'; states: {', '.join(g.states())}")
    return _move(run, state, g, args.state, args.reason, False, True)


def _base(cfg: dict[str, str]) -> Path:
    return Path(cfg.get("dir") or home()).expanduser().resolve()


_ALLOWED = {"view": (r"pseudocode|outline", "pseudocode or outline"),
            "detail": (r"short|full", "short or full"),
            "label_max": (r"[1-9][0-9]*", "a positive integer")}


def _cmd_config(args) -> int:
    cfg = load_config()
    if args.key is None:
        print(home() / "config.json")
        print(f"dir = {_base(cfg)}")
        for key in CONFIG_KEYS[1:]:
            print(f"{key} = {cfg.get(key) or '(not set)'}")
        return 0
    if args.value is None:
        print("usage: sm config [<key> <value>]", file=sys.stderr)
        return 2
    if args.key not in CONFIG_KEYS:
        raise ValueError(f"unknown key '{args.key}'; keys: {', '.join(CONFIG_KEYS)}")
    allowed = _ALLOWED.get(args.key)
    if args.value and allowed and not re.fullmatch(allowed[0], args.value):
        raise ValueError(f"bad value '{args.value}' for {args.key}; expected {allowed[1]}")
    if args.value:
        cfg[args.key] = args.value
    else:
        cfg.pop(args.key, None)
    save_config(cfg)
    return 0


def _cmd_new(args) -> int:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.slug):
        raise ValueError("slug must be letters, digits, '-' or '_'")
    d = _base(load_config()) / f"{datetime.now():%Y-%m-%d}-{args.slug}"
    if (d / "machine.dot").exists():
        raise ValueError(f"{d} already holds a machine.dot")
    text = None
    if args.src:
        src = Path(args.src).expanduser()
        src = src / "machine.dot" if src.is_dir() else src
        try:
            text = src.read_text()
        except FileNotFoundError:
            raise ValueError(f"no machine.dot at {args.src}")
        errors = _parse_lint(text)[1]
        if errors:
            raise _invalid(errors)
    d.mkdir(parents=True, exist_ok=True)
    if text is not None:
        (d / "machine.dot").write_text(text)
    print(d)
    return 0


def _cmd_finished(args) -> int:
    if not args.dirs:
        raise ValueError("no runs given")
    lines = []
    for d in args.dirs:
        run = Run(d)
        if not run.state_file.exists():
            lines.append(f"{run.dir}: not started")
            continue
        try:
            current, final = _position(run)
        except (ValueError, OSError, KeyError) as e:
            lines.append(f"{run.dir}: invalid ({e})")
            continue
        if not final:
            blocked = " (BLOCKED.md)" if (run.dir / "BLOCKED.md").exists() else ""
            lines.append(f"{run.dir}: at {current}{blocked}")
    if not lines:
        return 0
    print("\n".join(lines))
    print(f"{len(lines)} of {len(args.dirs)} runs not finished")
    return 1


def _cmd_log(args) -> int:
    run, _, _, _ = _open(strict=False)
    entries = [e for e in run.tail(sys.maxsize) if args.vars or e["kind"] != "var"]
    for e in entries[-args.n:] if args.n > 0 else []:
        hm = e["ts"][11:16]
        if e["kind"] == "edit":
            print(f"-- {hm} edit at step {e['step']}: {e['summary']}")
            continue
        if e["kind"] == "var":
            if e["op"] == "set":
                print(f"-- {hm} set {e['name']} = {_show(e['value'])}")
            elif e["op"] == "push":
                print(f"-- {hm} push {e['name']} + {', '.join(_show(i) for i in e['items'])}")
            else:
                print(f"-- {hm} pop {e['name']} -> {e['into']} = {_show(e['value'])}")
            continue
        line = f"{e['step']} {hm} {e['from']} -> {e['to']}"
        line += f" ({e['reason']})" if e["reason"] else ""
        line += " [check ok]" if e["check"] else ""
        line += " JUMP" if e["jump"] else ""
        print(line)
    return 0


def _cmd_set(args) -> int:
    name = _var_name(args.name)
    run = Run.active()
    with run.lock():
        state = run.state()
        state.setdefault("vars", {})[name] = args.value
        _save_var(run, state, op="set", name=name, value=_show(args.value, 200))
    return 0


def _cmd_push(args) -> int:
    name = _var_name(args.name)
    run = Run.active()
    with run.lock():
        state = run.state()
        items = state.setdefault("vars", {}).get(name, [])
        if not isinstance(items, list):
            raise ValueError(f"{name} is not a list")
        state["vars"][name] = items + args.values
        _save_var(run, state, op="push", name=name, items=[_show(v, 200) for v in args.values])
    return 0


def _cmd_pop(args) -> int:
    name, into = _var_name(args.name), _var_name(args.into)
    run = Run.active()
    with run.lock():
        state = run.state()
        values = state.setdefault("vars", {})
        items = values.get(name, [])
        if not isinstance(items, list):
            raise ValueError(f"{name} is not a list")
        if into == name or isinstance(values.get(into), list):
            raise ValueError(f"cannot pop into {into}: it is a list")
        if not items:
            raise ValueError(f"{name} is empty")
        value = items.pop(-1 if args.last else 0)
        values[into] = value
        _save_var(run, state, op="pop", name=name, value=_show(value, 200), into=into)
    return 0


def _cmd_get(args) -> int:
    run = Run(args.run) if args.run else Run.active()
    values = _vars(run.state())
    if args.name not in values:
        raise ValueError(f"{args.name} is not set")
    v = values[args.name]
    print("\n".join(v) if isinstance(v, list) else v)
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sm", description="Walk a Graphviz DOT state machine.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn)
        return sp

    add("validate", _cmd_validate, "parse and lint the machine").add_argument("dir", nargs="?")
    sp = add("show", _cmd_show, "pseudocode view, current state marked")
    views = sp.add_mutually_exclusive_group()
    views.add_argument("--outline", dest="view", action="store_const", const="outline",
                       help="ASCII outline instead of pseudocode")
    views.add_argument("--pseudocode", dest="view", action="store_const", const="pseudocode")
    detail = sp.add_mutually_exclusive_group()
    detail.add_argument("--full", dest="detail", action="store_const", const="full",
                        help="include check commands and do text")
    detail.add_argument("--short", dest="detail", action="store_const", const="short")
    sp.add_argument("dir", nargs="?")
    sp = add("png", _cmd_png, "render with Graphviz")
    sp.add_argument("dir", nargs="?")
    sp.add_argument("-o", "--output")
    sp = add("init", _cmd_init, "start a machine")
    sp.add_argument("dir")
    sp.add_argument("--cwd")
    sp.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    add("use", _cmd_use, "point this session at a machine").add_argument("dir")
    add("now", _cmd_now, "where we are and the exits")
    sp = add("go", _cmd_go, "move along an edge")
    sp.add_argument("state", nargs="?")
    sp.add_argument("-m", "--reason")
    sp = add("jump", _cmd_jump, "move to any state, bypassing edges")
    sp.add_argument("state")
    sp.add_argument("-m", "--reason", required=True)
    sp = add("new", _cmd_new, "create a dated directory for a new machine")
    sp.add_argument("slug")
    sp.add_argument("--from", dest="src", default=None)
    add("finished", _cmd_finished, "exit 0 if every run is at a final state").add_argument("dirs", nargs="*")
    sp = add("config", _cmd_config, "show or set user settings")
    sp.add_argument("key", nargs="?")
    sp.add_argument("value", nargs="?")
    sp = add("set", _cmd_set, "set a variable")
    sp.add_argument("name")
    sp.add_argument("value")
    sp = add("push", _cmd_push, "append values to a list variable")
    sp.add_argument("name")
    sp.add_argument("values", nargs="+")
    sp = add("pop", _cmd_pop, "remove a list's first (or --last) value into another variable")
    sp.add_argument("name")
    sp.add_argument("into")
    sp.add_argument("--last", action="store_true")
    sp = add("get", _cmd_get, "print a variable")
    sp.add_argument("--run")
    sp.add_argument("name")
    sp = add("log", _cmd_log, "recent transitions")
    sp.add_argument("-n", type=int, default=10)
    sp.add_argument("--vars", action="store_true", help="include variable changes")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, OSError) as e:
        print(e, file=sys.stderr)
        return 1
