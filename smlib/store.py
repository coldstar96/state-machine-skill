from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from smlib.dot import Graph


def _edges(g: Graph) -> dict[tuple[str, str], dict]:
    states = set(g.states())
    return {(e["src"], e["dst"]): e for e in g.edges if e["src"] in states and e["dst"] in states}


def _hooks(g: Graph) -> dict[str, str]:
    return {k: v for k, v in g.attrs.items() if k.startswith("on_")}


def changes(old: Graph, new: Graph) -> list[str]:
    os_, ns = old.states(), new.states()
    oe, ne = _edges(old), _edges(new)
    both = [s for s in ns if s in os_]
    both_e = [k for k in ne if k in oe]
    out = [f"+state {s}" for s in ns if s not in os_]
    out += [f"-state {s}" for s in os_ if s not in ns]
    out += [f"+edge {a}->{b}" for a, b in ne if (a, b) not in oe]
    out += [f"-edge {a}->{b}" for a, b in oe if (a, b) not in ne]
    out += [f"{s}: label changed" for s in both if old.label(s) != new.label(s)]
    out += [f"{s}: do changed" for s in both if old.detail(s) != new.detail(s)]
    out += [f"{s}: final changed" for s in both if old.is_final(s) != new.is_final(s)]
    out += [f"{a}->{b}: label changed" for a, b in both_e if oe[a, b].get("label") != ne[a, b].get("label")]
    out += [f"{a}->{b}: check changed" for a, b in both_e if oe[a, b].get("check") != ne[a, b].get("check")]
    out += [f"{a}->{b}: ask changed" for a, b in both_e if oe[a, b].get("ask") != ne[a, b].get("ask")]
    if _hooks(old) != _hooks(new):
        out.append("hooks changed")
    if old.initial() != new.initial():
        out.append(f"initial: {old.initial()} -> {new.initial()}")
    return out


HOOKS = ("on_move", "on_finish", "on_check_fail", "on_ask", "on_blocked", "on_edit", "on_var")
CONFIG_KEYS = ("dir", *HOOKS, "view", "detail", "label_max")


def home() -> Path:
    return Path(os.environ.get("SM_HOME") or Path.home() / ".claude" / "state-machines")


def load_config() -> dict[str, str]:
    try:
        text = (home() / "config.json").read_text()
    except FileNotFoundError:
        return {}
    try:
        cfg = json.loads(text)
    except ValueError as e:
        raise ValueError(f"config.json: {e}")
    if not isinstance(cfg, dict):
        raise ValueError("config.json: must be a JSON object")
    for k, v in cfg.items():
        if not isinstance(v, str):
            raise ValueError(f"config.json: {k} must be a string")
    return cfg


def save_config(cfg: dict[str, str]) -> None:
    home().mkdir(parents=True, exist_ok=True)
    path = home() / "config.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    os.replace(tmp, path)


def _pointer() -> Path:
    key = os.environ.get("CLAUDE_CODE_SESSION_ID") or "default"
    return home() / ".active" / re.sub(r"[^A-Za-z0-9_-]", "_", key)


class Run:
    def __init__(self, directory) -> None:
        self.dir = Path(os.path.abspath(directory))
        self.dot = self.dir / "machine.dot"
        self.applied = self.dir / "machine.applied.dot"
        self.state_file = self.dir / "state.json"
        self.history_file = self.dir / "history.jsonl"

    @classmethod
    def active(cls) -> "Run":
        try:
            run = cls(os.environ.get("SM_RUN") or _pointer().read_text().strip())
        except OSError:
            run = None
        if run is None or not run.dir.is_dir():
            raise ValueError("no active machine: sm init <dir> or sm use <dir>")
        return run

    def point(self) -> str | None:
        if os.environ.get("SM_RUN"):
            pinned = Run(os.environ["SM_RUN"]).dir
            if pinned != self.dir:
                raise ValueError(f"SM_RUN is {pinned}, not {self.dir}")
            return None
        p = _pointer()
        try:
            prev = p.read_text().strip() or None
        except OSError:
            prev = None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(self.dir))
        return None if prev == str(self.dir) else prev

    def state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except FileNotFoundError:
            raise ValueError(f"no state file in {self.dir}: sm init {self.dir}")

    def save(self, state: dict) -> None:
        tmp = self.state_file.with_name(self.state_file.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, self.state_file)

    @contextmanager
    def lock(self):
        with (self.dir / ".lock").open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def log(self, **entry) -> None:
        entry["ts"] = datetime.now().isoformat(timespec="seconds")
        with self.history_file.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def tail(self, n: int) -> list[dict]:
        try:
            lines = self.history_file.read_text().splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(x) for x in lines[-n:]] if n > 0 else []
