from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


class CliTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()

    def sm(self, *args, env=None):
        full = {k: v for k, v in os.environ.items() if k not in {"CLAUDE_CODE_SESSION_ID", "SM_RUN"}}
        full["SM_HOME"] = str(self.tmp)
        full.update(env or {})
        p = subprocess.run([sys.executable, str(ROOT / "sm.py"), *map(str, args)], env=full,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           encoding="utf-8", errors="replace")
        return p.returncode, p.stdout + p.stderr

    def copy_fixture(self, name, edit=None):
        d = self.tmp / "m"
        d.mkdir(exist_ok=True)
        text = (FIXTURES / name).read_text()
        (d / "machine.dot").write_text(edit(text) if edit else text)
        return d

    def start(self, fixture, edit=None, cwd=None, env=None):
        d = self.copy_fixture(fixture, edit)
        return self.sm("init", d, *(["--cwd", cwd] if cwd else []), env=env)

    def rewrite(self, fn):
        p = self.tmp / "m" / "machine.dot"
        p.write_text(fn(p.read_text()))

    def test_sm_run_overrides_the_session_pointer(self):
        self.start("chain.dot")
        child = self.tmp / "child"
        child.mkdir()
        (child / "machine.dot").write_text((FIXTURES / "chain.dot").read_text())
        env = {"SM_RUN": str(child)}
        code, out = self.sm("init", child, env=env)
        self.assertEqual(code, 0, out)
        self.assertNotIn("note: this session was on", out)
        self.assertEqual(self.sm("go", env=env)[0], 0)
        self.assertIn("b · step 1", self.sm("now", env=env)[1])
        self.assertIn("a · step 0", self.sm("now")[1])
        self.assertIn("a · step 0", self.sm("now", env={"SM_RUN": ""})[1])
        self.assertEqual((self.tmp / ".active" / "default").read_text().strip(), str(self.tmp / "m"))
        code, out = self.sm("now", env={"SM_RUN": str(self.tmp / "gone")})
        self.assertEqual(code, 1)
        self.assertIn("no active machine", out)
        code, out = self.sm("use", self.tmp / "m", env=env)
        self.assertEqual(code, 1)
        self.assertIn("SM_RUN is", out)
        other = self.tmp / "other"
        other.mkdir()
        (other / "machine.dot").write_text((FIXTURES / "chain.dot").read_text())
        code, out = self.sm("init", other, "--set", "a=b", env=env)
        self.assertEqual(code, 1)
        self.assertIn("SM_RUN is", out)
        self.assertFalse((other / "state.json").exists())
        self.assertFalse((other / "history.jsonl").exists())

    def test_vars_set_push_pop_get(self):
        self.start("chain.dot")
        self.assertEqual(self.sm("set", "item", "x")[0], 0)
        self.assertEqual(self.sm("get", "item"), (0, "x\n"))
        self.assertEqual(self.sm("set", "item", "--", "-x.py")[0], 0)
        self.assertEqual(self.sm("get", "item"), (0, "-x.py\n"))
        self.assertEqual(self.sm("push", "queue", "a", "b", "c")[0], 0)
        self.assertEqual(self.sm("push", "queue", "d")[0], 0)
        self.assertEqual(self.sm("pop", "queue", "item")[0], 0)
        self.assertEqual(self.sm("get", "item"), (0, "a\n"))
        self.assertEqual(self.sm("pop", "queue", "item", "--last")[0], 0)
        self.assertEqual(self.sm("get", "item"), (0, "d\n"))
        self.assertEqual(self.sm("get", "queue"), (0, "b\nc\n"))
        self.sm("pop", "queue", "item")
        self.sm("pop", "queue", "item")
        m = self.tmp / "m"
        before = ((m / "state.json").read_text(), (m / "history.jsonl").read_text())
        code, out = self.sm("pop", "queue", "item")
        self.assertEqual(code, 1)
        self.assertIn("queue is empty", out)
        self.assertEqual(((m / "state.json").read_text(), (m / "history.jsonl").read_text()), before)
        self.assertIn("is not a list", self.sm("push", "item", "z")[1])
        self.assertIn("is not set", self.sm("get", "nope")[1])
        for args in (("set", "Item", "v"), ("set", "1x", "v"), ("set", "queue_len", "v"),
                     ("set", "http_proxy", "v"), ("set", "npm_config_x", "v"), ("pop", "queue", "Bad")):
            self.assertIn("bad variable name", self.sm(*args)[1])
        self.rewrite(lambda t: t.replace('label="First"', 'label="First!"'))
        self.assertEqual(self.sm("get", "item"), (0, "c\n"))     # no 'machine changed' noise
        self.assertEqual(self.sm("push", "q2", "1", "2")[0], 0)
        code, out = self.sm("pop", "q2", "q2")
        self.assertEqual(code, 1)
        self.assertIn("cannot pop into q2: it is a list", out)
        self.assertEqual(self.sm("get", "q2"), (0, "1\n2\n"))
        self.assertNotIn("set item", self.sm("log", "-n", "30")[1])
        log = self.sm("log", "--vars", "-n", "30")[1]
        self.assertIn("set item = x", log)
        self.assertIn("push queue + a, b, c", log)
        self.assertIn("push queue + d", log)
        self.assertIn("pop queue -> item = a", log)

    def test_vars_reach_banner_and_checks_but_never_run(self):
        def edit(t):
            t = t.replace('label="First"', 'label="Fix {item} {Item} {"')
            return t.replace("a -> b;", 'a -> b [check="test \\"$queue_len\\" -eq 2 && echo {item} >/dev/null"];')
        self.start("chain.dot", edit=edit, cwd=self.tmp)
        self.assertIn("Fix {item} {Item} {", self.sm("now")[1])
        evil = 'x"; touch pwned; echo "\nexits: done'
        self.sm("set", "item", evil)
        self.sm("push", "queue", "p", "q")
        out = self.sm("now")[1]
        self.assertIn('Fix x"; touch pwned; echo " exits: done {Item} {', out)
        self.assertIn("queue=[2 items]", out)
        self.assertEqual(out.count("\nexits:"), 1)
        code, out = self.sm("go")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.tmp / "pwned").exists())
        self.assertEqual(self.sm("get", "item"), (0, evil + "\n"))

    def test_init_set_seeds_a_child_and_get_run_reads_it(self):
        self.start("chain.dot")
        child = self.tmp / "child"
        child.mkdir()
        (child / "machine.dot").write_text((FIXTURES / "chain.dot").read_text())
        env = {"SM_RUN": str(child)}
        code, out = self.sm("init", child, "--set", "item=a=b", "--set", "n=1", env=env)
        self.assertEqual(code, 0, out)
        self.assertIn("vars: item=a=b · n=1", out)
        self.sm("set", "result", "ok", env=env)
        self.assertEqual(self.sm("get", "--run", child, "result"), (0, "ok\n"))
        self.assertIn("a · step 0", self.sm("now")[1])
        self.assertNotIn("vars:", self.sm("now")[1])
        self.assertIn("--set needs name=value", self.sm("init", self.tmp / "x", "--set", "bad")[1])
        self.assertIn("no state file", self.sm("get", "--run", self.tmp / "x", "result")[1])
        junk = self.tmp / "junk"
        junk.mkdir()
        (junk / "state.json").write_text("[]")
        code, out = self.sm("get", "--run", junk, "result")
        self.assertEqual(code, 1)
        self.assertIn("result is not set", out)

    def test_parallel_sets_all_land(self):
        self.start("chain.dot")
        env = {k: v for k, v in os.environ.items() if k not in {"CLAUDE_CODE_SESSION_ID", "SM_RUN"}}
        env["SM_HOME"] = str(self.tmp)
        procs = [subprocess.Popen([sys.executable, str(ROOT / "sm.py"), "set", f"v{i}", str(i)], env=env,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for i in range(12)]
        self.assertEqual([p.wait() for p in procs], [0] * 12)
        for p in procs:
            p.stdout.close()
            p.stderr.close()
        got = json.loads((self.tmp / "m" / "state.json").read_text())["vars"]
        self.assertEqual(set(got), {f"v{i}" for i in range(12)})

    def test_finished_checks_every_run(self):
        def run(name, moves):
            d = self.tmp / name
            d.mkdir()
            (d / "machine.dot").write_text((FIXTURES / "chain.dot").read_text())
            env = {"SM_RUN": str(d)}
            self.assertEqual(self.sm("init", d, env=env)[0], 0)
            for _ in range(moves):
                self.assertEqual(self.sm("go", env=env)[0], 0)
            return d
        done1, done2, mid, broken = run("r1", 2), run("r2", 2), run("r3", 1), run("r4", 0)
        self.assertEqual(self.sm("finished", done1, done2), (0, ""))
        (mid / "BLOCKED.md").write_text("# Blocked at b\n")
        (broken / "machine.dot").write_text("digraph x { a -> }")
        code, out = self.sm("finished", done1, mid, self.tmp / "nope", broken)
        self.assertEqual(code, 1)
        self.assertIn(f"{mid}: at b (BLOCKED.md)", out)
        self.assertIn(f"{self.tmp / 'nope'}: not started", out)
        self.assertIn(f"{broken}: invalid (", out)
        self.assertNotIn(f"{done1}:", out)
        self.assertTrue(out.rstrip().endswith("3 of 4 runs not finished"))
        code, out = self.sm("finished")
        self.assertEqual(code, 1)
        self.assertIn("no runs given", out)

    def test_new_from_copies_only_the_machine(self):
        src = self.copy_fixture("chain.dot")
        self.assertEqual(self.sm("init", src)[0], 0)
        before = (src / "machine.dot").read_bytes()
        code, out = self.sm("new", "reuse", "--from", src)
        self.assertEqual(code, 0, out)
        made = Path(out.strip())
        self.assertEqual(sorted(p.name for p in made.iterdir()), ["machine.dot"])
        self.assertEqual((made / "machine.dot").read_bytes(), before)
        self.assertEqual((src / "machine.dot").read_bytes(), before)
        code, out = self.sm("new", "reuse2", "--from", FIXTURES / "loop.dot")
        self.assertEqual(code, 0, out)
        self.assertEqual((Path(out.strip()) / "machine.dot").read_text(), (FIXTURES / "loop.dot").read_text())

    def test_new_from_refuses_bad_sources_without_creating_a_dir(self):
        today = datetime.now().strftime("%Y-%m-%d")
        bad = self.tmp / "bad.dot"
        bad.write_text("digraph x { a -> }")
        for src, why in ((bad, "invalid"), (self.tmp / "missing", "no machine.dot"), (self.tmp, "no machine.dot")):
            code, out = self.sm("new", "nope", "--from", src)
            self.assertEqual(code, 1)
            self.assertIn(why, out)
        self.assertFalse((self.tmp / f"{today}-nope").exists())

    def test_walk_chain_to_the_end(self):
        self.start("chain.dot")
        self.assertIn("[chain] a · step 0 · visit 1 — ", self.sm("now")[1])
        self.assertEqual(self.sm("go")[0], 0)
        self.assertEqual(self.sm("go", "c")[0], 0)
        self.assertIn("final state — machine finished", self.sm("now")[1])
        code, out = self.sm("go")
        self.assertEqual(code, 1)
        self.assertIn("machine finished", out)
        self.assertEqual(len(self.sm("log")[1].splitlines()), 2)

    def test_banner_restates_the_rule_on_non_final_states_only(self):
        self.start("chain.dot")
        self.assertTrue(self.sm("now")[1].rstrip().endswith(
            "rule: only this state's work; if it does not fit, stop and report (BLOCKED.md), no jump, no graph edits"))
        self.sm("go")
        self.sm("go", "c")
        self.assertNotIn("rule:", self.sm("now")[1])

    def test_go_refuses_a_non_edge(self):
        self.start("loop.dot")
        code, out = self.sm("go", "verify")
        self.assertEqual(code, 1)
        self.assertIn("no edge reproduce -> verify; exits: diagnose", out)
        self.assertIn("reproduce · step 0", self.sm("now")[1])

    def test_check_gates_the_move(self):
        self.start("loop.dot", edit=lambda t: t.replace("npm test", "false"))
        for _ in range(3):
            self.sm("go")
        code, out = self.sm("go", "done")
        self.assertEqual(code, 1)
        self.assertIn("check failed (exit 1): false", out)
        self.assertIn("verify · step 3", self.sm("now")[1])
        self.rewrite(lambda t: t.replace('check="false"', r'''check="printf '\377\376'"'''))
        self.sm("now")
        self.assertEqual(self.sm("go", "done")[0], 0)
        self.assertIn("[check ok]", self.sm("log")[1])

    def test_reason_required_on_a_judgment_branch(self):
        self.start("loop.dot")
        for _ in range(3):
            self.sm("go")
        code, out = self.sm("go")
        self.assertEqual(code, 1)
        self.assertIn("verify has 2 exits: done, fix; sm go <state>", out)
        code, out = self.sm("go", "fix")
        self.assertEqual(code, 1)
        self.assertIn("reason required", out)
        self.assertEqual(self.sm("go", "fix", "-m", "2 tests red")[0], 0)
        self.assertIn("fix · step 4 · visit 2", self.sm("now")[1])

    def test_mid_run_edit_blocks_one_go_and_keeps_position(self):
        self.start("loop.dot")
        self.sm("go")
        self.rewrite(lambda t: t.replace("Find the root cause; no fix yet", "Find the root cause"))
        code, out = self.sm("go")
        self.assertEqual(code, 1)
        self.assertIn("machine changed at step 1: diagnose: label changed", out)
        self.assertIn("diagnose · step 1", out)
        self.assertEqual(self.sm("go")[0], 0)
        self.assertIn("edit at step 1", self.sm("log")[1])

    def test_do_reaches_the_banner_filled_and_a_mid_run_edit_notifies(self):
        self.start("chain.dot", edit=lambda t: t.replace('label="First"', 'label="First", do="Fix {item}\\nnow"'))
        self.sm("set", "item", "x")
        out = self.sm("now")[1].splitlines()
        self.assertIn("— First", out[0])
        self.assertEqual(out[1], "do: Fix x now")
        self.assertTrue(out[2].startswith("vars:"))
        self.rewrite(lambda t: t.replace("Fix {item}", "Mend {item}"))
        code, out = self.sm("go")
        self.assertEqual(code, 1)
        self.assertIn("machine changed at step 0: a: do changed", out)
        self.assertEqual(self.sm("go")[0], 0)

    def test_invalid_edit_blocks_moves_but_not_show(self):
        self.start("loop.dot")
        self.rewrite(lambda t: t.replace("diagnose  -> fix;", ""))
        code, out = self.sm("go")
        self.assertEqual(code, 1)
        self.assertIn("machine.dot is invalid:", out)
        self.assertEqual(self.sm("show")[0], 0)

    def test_removed_current_state_needs_jump(self):
        self.start("chain.dot")
        self.rewrite(lambda t: re.sub(r"\ba\b", "a2", t))
        code, out = self.sm("now")
        self.assertEqual(code, 1)
        self.assertIn("is no longer in the graph", out)
        code, out = self.sm("jump", "zz", "-m", "typo")
        self.assertEqual(code, 1)
        self.assertIn("no such state 'zz'", out)
        self.assertEqual(self.sm("jump", "start", "-m", "entry")[0], 1)
        self.assertEqual(self.sm("jump", "a2", "-m", "renamed")[0], 0)
        self.assertIn("JUMP", self.sm("log")[1])

    def test_check_with_stdin_and_long_output(self):
        self.start("loop.dot", edit=lambda t: t.replace("npm test", "cat; seq 1 1000; false"))
        for _ in range(3):
            self.sm("go")
        code, out = self.sm("go", "done")
        self.assertEqual(code, 1)
        self.assertLessEqual(len(out.splitlines()), 20)
        self.assertIn("\n986\n", out)
        self.assertNotIn("\n985\n", out)

    def test_deleted_check_directory(self):
        gone = self.tmp / "gone"
        gone.mkdir()
        self.start("loop.dot", cwd=gone)
        for _ in range(3):
            self.sm("go")
        gone.rmdir()
        code, out = self.sm("go", "done")
        self.assertEqual(code, 1)
        self.assertIn("check directory is gone", out)
        self.assertNotIn("Traceback", out)

    def test_no_active_machine(self):
        code, out = self.sm("now")
        self.assertEqual(code, 1)
        self.assertIn("no active machine: sm init <dir> or sm use <dir>", out)
        self.start("chain.dot")
        shutil.rmtree(self.tmp / "m")
        code, out = self.sm("now")
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", out)
        code, out = self.start("chain.dot")
        self.assertEqual(code, 0)
        self.assertNotIn("note:", out)
        (self.tmp / "empty").mkdir()
        code, out = self.sm("validate", self.tmp / "empty")
        self.assertEqual(code, 1)
        self.assertIn("no machine.dot in", out)
        self.assertNotIn("Traceback", out)

    def test_show_and_png_before_start(self):
        d = self.copy_fixture("loop.dot")
        self.assertIn("  LOOP\n", self.sm("show", d)[1])
        self.assertIn("IF (suite green)  ⚙  BREAK", self.sm("show", d)[1])
        self.assertNotIn("npm test", self.sm("show", d)[1])
        self.assertIn("IF (suite green)  ⚙ npm test  BREAK", self.sm("show", "--full", d)[1])
        self.assertRegex(self.sm("show", "--outline", "--full", d)[1], r"suite green\s+⚙ npm test")
        self.assertIn("○ reproduce", self.sm("show", "--outline", d)[1])
        self.assertEqual(self.sm("validate", d)[0], 0)
        link = self.tmp / "sm_link.py"
        link.symlink_to(ROOT / "sm.py")
        p = subprocess.run([sys.executable, str(link), "validate", str(d)], env={"SM_HOME": str(self.tmp)},
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8")
        self.assertEqual(p.returncode, 0)
        self.assertIn("ok: 5 states", p.stdout)
        empty = self.tmp / "nopath"
        empty.mkdir()
        code, out = self.sm("png", d, env={"PATH": str(empty)})
        self.assertEqual(code, 3)
        self.assertIn("Graphviz not found: sm png needs the dot command on PATH", out)
        if shutil.which("dot"):
            code, out = self.sm("png", d)
            self.assertEqual(code, 0)
            path = Path(out.strip())
            self.assertTrue(path.is_absolute())
            self.assertEqual(path.read_bytes()[:4], b"\x89PNG")

    def test_new_creates_a_dated_directory_under_dir(self):
        today = datetime.now().strftime("%Y-%m-%d")
        work = self.tmp / "work"
        self.assertEqual(self.sm("config", "dir", work)[0], 0)
        code, out = self.sm("new", "fix-mean")
        self.assertEqual(code, 0)
        made = work / f"{today}-fix-mean"
        self.assertEqual(out.strip(), str(made))
        self.assertTrue(made.is_dir())
        self.assertEqual(self.sm("new", "bad slug")[0], 1)
        (made / "machine.dot").write_text("digraph x {}")
        self.assertEqual(self.sm("new", "fix-mean")[0], 1)
        home = self.tmp / "home"
        home.mkdir()
        self.sm("config", "dir", "~/w")
        code, out = self.sm("new", "x", env={"HOME": str(home)})
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().startswith(str(home / "w")))

    def test_config_shows_sets_and_refuses_unknown_keys(self):
        code, out = self.sm("config")
        self.assertEqual(code, 0)
        self.assertIn("dir = ", out)
        self.assertIn("on_move = (not set)", out)
        self.assertEqual(self.sm("config", "on_move", "true")[0], 0)
        self.assertIn("on_move = true", self.sm("config")[1])
        self.sm("config", "on_move", "")
        self.assertIn("on_move = (not set)", self.sm("config")[1])
        code, out = self.sm("config", "nope", "x")
        self.assertEqual(code, 1)
        self.assertIn("dir, on_move", out)
        self.assertEqual(self.sm("config", "dir")[0], 2)
        (self.tmp / "config.json").write_text('{"dir": 3}')
        code, out = self.sm("config")
        self.assertEqual(code, 1)
        self.assertIn("config.json:", out)
        (self.tmp / "config.json").write_text("{nope")
        code, out = self.sm("config")
        self.assertEqual(code, 1)
        self.assertIn("config.json:", out)

    def test_config_validates_view_detail_label_max_and_lists_every_key(self):
        for key, bad in (("view", "table"), ("detail", "long"), ("label_max", "0"), ("label_max", "ten")):
            code, out = self.sm("config", key, bad)
            self.assertEqual(code, 1)
            self.assertIn(f"bad value '{bad}' for {key}; expected ", out)
        out = self.sm("config")[1]
        for key in ("view", "detail", "label_max"):
            self.assertIn(f"{key} = (not set)", out)
        for key, good in (("view", "outline"), ("detail", "full"), ("label_max", "25")):
            self.assertEqual(self.sm("config", key, good)[0], 0)
            self.assertIn(f"{key} = {good}", self.sm("config")[1])
        self.sm("config", "view", "")
        self.assertIn("view = (not set)", self.sm("config")[1])

    def test_settings_pick_the_default_show_and_flags_override(self):
        d = self.copy_fixture("loop.dot")
        self.sm("config", "view", "outline")
        self.assertIn("○ reproduce", self.sm("show", d)[1])
        self.assertIn("  LOOP\n", self.sm("show", "--pseudocode", d)[1])
        self.sm("config", "view", "pseudocode")
        self.sm("config", "detail", "full")
        self.assertIn("⚙ npm test", self.sm("show", d)[1])
        self.assertNotIn("npm test", self.sm("show", "--short", d)[1])
        self.assertIn("⚙ npm test", self.sm("show", "--outline", d)[1])
        self.assertEqual(self.sm("show", "--outline", "--pseudocode", d)[0], 2)
        self.assertEqual(self.sm("show", "--full", "--short", d)[0], 2)

    def test_label_max_setting_sets_the_lint_note(self):
        d = self.copy_fixture("chain.dot", edit=lambda t: t.replace('"Second"', '"Second one"'))
        self.assertEqual(self.sm("validate", d)[1].count("note:"), 0)
        self.sm("config", "label_max", "10")
        self.assertNotIn("note:", self.sm("validate", d)[1])
        self.sm("config", "label_max", "9")
        code, out = self.sm("validate", d)
        self.assertEqual(code, 0)
        self.assertIn("note: state b: label is 10 chars; keep it short, put detail in do=", out)

    def test_show_survives_a_corrupt_config(self):
        d = self.copy_fixture("chain.dot")
        (self.tmp / "config.json").write_text("{nope")
        code, out = self.sm("show", d)
        self.assertEqual(code, 0)
        self.assertIn('@a:', out)

    def test_on_move_gets_the_state_and_never_blocks(self):
        self.sm("config", "on_move",
                'echo "$SM_STATE $SM_MACHINE $SM_STEP" >> "$SM_HOME/moves.log"; echo noise; exit 7')
        self.start("chain.dot")
        code, out = self.sm("go")
        self.assertEqual(code, 0)
        self.assertNotIn("noise", out)
        self.assertEqual((self.tmp / "moves.log").read_text().splitlines(), ["a chain 0", "b chain 1"])
        (self.tmp / "config.json").write_text("{nope")
        code, out = self.sm("go")
        self.assertEqual(code, 0)
        self.assertIn("[chain] c", out)


if __name__ == "__main__":
    unittest.main()
