from __future__ import annotations

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
        full = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
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
        self.assertIn("○ reproduce", self.sm("show", d)[1])
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
