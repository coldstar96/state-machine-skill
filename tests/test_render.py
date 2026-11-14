from __future__ import annotations

import unittest
from pathlib import Path

from smlib.dot import parse
from smlib.render import highlighted, outline, spaced


def read(name):
    return (Path(__file__).parent / "fixtures" / name).read_text()


class RenderTests(unittest.TestCase):
    def test_outline_matches_the_spec_sketch(self):
        out = outline(parse(read("loop.dot")), "diagnose")
        lines = out.splitlines()
        self.assertEqual(lines[0], "fix_bug")
        self.assertRegex(out, r"● diagnose\s+Find the root cause; no fix yet\s+◀ current")
        self.assertRegex(out, r"○ reproduce\s+Reproduce the bug with a failing test")
        self.assertRegex(out, r"├─▶ done\s+suite green\s+⚙ npm test")
        self.assertRegex(out, r"└─↩ fix\s+suite red")
        self.assertRegex(out, r"◎ done\s+Report the result")
        self.assertEqual(out.count("◀ current"), 1)

    def test_cycle_outline_terminates(self):
        out = outline(parse(read("cycle.dot")))
        self.assertRegex(out, r"└─↩ poll")
        self.assertEqual(len(out.splitlines()), 5)

    def test_broken_machine_still_renders(self):
        out = outline(parse(read("loop.dot").replace("diagnose  -> fix;", "")))
        self.assertRegex(out, r"unreachable:\n(.*\n)*.*fix\s+Apply the minimal fix")
        self.assertIn("unreachable:", outline(parse('digraph g { a [label=x]; a -> a; }')))

    def test_undeclared_target_does_not_crash(self):
        out = outline(parse('digraph g { s [shape=point]; s -> a; a [label=A]; a -> ghost; }'))
        self.assertRegex(out, r"└─▶ ghost")

    def test_highlighted_injects_before_last_brace(self):
        out = highlighted(read("loop.dot"), "fix")
        self.assertTrue(out.rstrip().endswith('fix [style=filled, fillcolor="#ffd54f"];\n}'))
        self.assertEqual(highlighted("digraph g { }", None), "digraph g { }")

    def test_spaced_default_comes_before_the_users_own_setting(self):
        self.assertEqual(spaced("digraph g { nodesep=0.1; }"), "digraph g { nodesep=1.2; nodesep=0.1; }")


if __name__ == "__main__":
    unittest.main()
