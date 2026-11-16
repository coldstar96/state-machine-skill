from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smlib.dot import parse
from smlib.store import Run, changes


def read(name):
    return (Path(__file__).parent / "fixtures" / name).read_text()


class StoreTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.home = self.tmp / "home"
        (self.tmp / "a").mkdir()
        (self.tmp / "b").mkdir()
        env = patch.dict(os.environ, {"SM_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        os.environ.pop("SM_RUN", None)

    def test_changes_summary(self):
        old = parse(read("loop.dot"))
        new = parse(read("loop.dot")
                    .replace('verify    -> done [label="suite green", check="npm test"];',
                             'verify -> review [label="suite green", check="npm test"];\n  review [label="Review the diff"];\n  review -> done;')
                    .replace("Apply the minimal fix", "Apply the fix"))
        self.assertEqual(changes(old, new), ["+state review", "+edge verify->review", "+edge review->done",
                                             "-edge verify->done", "fix: label changed"])
        self.assertEqual(changes(old, parse(read("loop.dot").replace("digraph fix_bug {", "digraph fix_bug {\n  rankdir=LR; // x"))), [])

    def test_hook_changes_are_reported_other_graph_attrs_are_not(self):
        src = "digraph g { %s a -> b; }"
        old = parse(src % "graph [on_move=x, rankdir=LR];")
        self.assertEqual(changes(old, parse(src % "graph [on_move=y, rankdir=TB];")), ["hooks changed"])
        self.assertEqual(changes(old, parse(src % "graph [on_move=x, rankdir=TB];")), [])
        self.assertEqual(changes(old, parse(src % "")), ["hooks changed"])

    def test_pointer_is_per_session_key(self):
        a, b = Run(self.tmp / "a"), Run(self.tmp / "b")
        self.assertIsNone(a.point())
        self.assertEqual(Run.active().dir, a.dir)
        self.assertEqual(b.point(), str(a.dir))
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "../x/y"}):
            with self.assertRaisesRegex(ValueError, "no active machine"):
                Run.active()
            a.point()
            self.assertTrue((self.home / ".active" / "___x_y").is_file())


    def test_ask_changes_are_reported(self):
        old = parse('digraph g { a [label=A]; b [label=B]; a -> b [ask="ok?"]; }')
        self.assertEqual(changes(old, parse('digraph g { a [label=A]; b [label=B]; a -> b; }')), ["a->b: ask changed"])
        self.assertEqual(changes(old, old), [])


if __name__ == "__main__":
    unittest.main()
