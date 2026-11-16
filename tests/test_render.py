from __future__ import annotations

import re
import unittest
from pathlib import Path

from smlib.dot import parse
from smlib.render import highlighted, outline, pseudocode, spaced


def read(name):
    return (Path(__file__).parent / "fixtures" / name).read_text()

NESTED = ("digraph g { s [shape=point]; s -> a; a [label=A]; b [label=B]; c [label=C]; d [label=D, shape=doublecircle];"
          " a -> b; b -> b [label=retry]; b -> c [label=ok]; c -> a [label=more]; c -> d [label=none]; }")


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

    def test_pseudo_loop_golden(self):
        self.assertEqual(pseudocode(parse(read("loop.dot")), "diagnose"), "\n".join([
            "fix_bug",
            '  @reproduce:  "Reproduce the bug with a failing test"',
            "    IF (test fails for the right reason)  GOTO @diagnose",
            '  @diagnose:   "Find the root cause; no fix yet"  ◀ current',
            "  LOOP",
            '    @fix:        "Apply the minimal fix"',
            '    @verify:     "Run the full suite"',
            "      IF (suite green)  ⚙ npm test  BREAK",
            "      IF (suite red)  CONTINUE",
            '  @done:       "Report the result"  END',
        ]))

    def test_pseudo_cycle_golden(self):
        self.assertEqual(pseudocode(parse(read("cycle.dot"))),
                         'watch\n  LOOP\n    @poll:  "Check for new work"\n    @act:   "Handle it"')

    def test_pseudo_nested_loops(self):
        self.assertEqual(pseudocode(parse(NESTED)), "\n".join([
            "g",
            "  LOOP",
            '    @a:  "A"',
            "    LOOP",
            '      @b:  "B"',
            "        IF (retry)  CONTINUE",
            "        IF (ok)  BREAK",
            '    @c:  "C"',
            "      IF (more)  CONTINUE",
            "      IF (none)  BREAK",
            '  @d:  "D"  END',
        ]))
        arm = ("digraph g { s [shape=point]; s -> h; h [label=H]; x [label=X]; y [label=Y, shape=doublecircle];"
               " z [label=Z, shape=doublecircle]; h -> x [label=a]; h -> y [label=b]; h -> z [label=c]; x -> h; }")
        self.assertEqual(pseudocode(parse(arm)), "\n".join([
            "g",
            "  LOOP",
            '    @h:  "H"',
            "      IF (a)",
            '        @x:  "X"',
            "          CONTINUE",
            "      IF (b)  BREAK",
            "      IF (c)  GOTO @z",
            '  @y:  "Y"  END',
            '  @z:  "Z"  END',
        ]))

    def test_pseudo_branch_merge(self):
        dot = ("digraph g { s [shape=point]; s -> t; t [label=Triage]; q [label=Q]; f [label=F];"
               " r [label=R, shape=doublecircle]; t -> q [label=flaky]; t -> f [label=real]; q -> r; f -> r; }")
        self.assertEqual(pseudocode(parse(dot)), "\n".join([
            "g",
            '  @t:  "Triage"',
            "    IF (flaky)",
            '      @q:  "Q"',
            "        GOTO @r",
            "    IF (real)",
            '      @f:  "F"',
            "        GOTO @r",
            '  @r:  "R"  END',
        ]))

    def test_pseudo_irreducible_uses_goto(self):
        dot = ("digraph g { s [shape=point]; s -> a; a [label=A]; b [label=B]; c [label=C];"
               " a -> b [label=x]; a -> c [label=y]; b -> c; c -> b; }")
        self.assertEqual(pseudocode(parse(dot)), "\n".join([
            "g",
            '  @a:  "A"',
            "    IF (x)  GOTO @b",
            "    IF (y)  GOTO @c",
            '  @c:  "C"',
            '  @b:  "B"',
            "    GOTO @c",
        ]))

    def test_pseudo_every_state_once(self):
        dup = ("digraph g { s [shape=point]; s -> a; a [label=A]; b [label=B];"
               " a -> b [label=one]; a -> b [label=two]; }")
        for dot, states in ((NESTED, "abcd"), (dup, "ab")):
            out = pseudocode(parse(dot))
            for state in states:
                self.assertEqual(len(re.findall(rf"^\s+@{state}:", out, re.M)), 1, state)
        out = pseudocode(parse(dup))
        self.assertIn("IF (one)", out)
        self.assertIn("IF (two)  GOTO @b", out)

    def test_pseudo_broken_machine_still_renders(self):
        out = pseudocode(parse("digraph g { a [label=x]; a -> a; }"))
        self.assertIn("-- unreachable --", out)
        self.assertIn("GOTO @a", out)
        self.assertIn("GOTO @ghost", pseudocode(parse("digraph g { s [shape=point]; s -> a; a [label=A]; a -> ghost; }")))
        out = pseudocode(parse("digraph g { s [shape=point]; s -> ghost; a [label=A]; }"))
        self.assertRegex(out, r"-- unreachable --\n\s+@a:")
        self.assertNotRegex(out, r"(?m)^\s+@ghost:")
        out = pseudocode(parse("digraph g { s [shape=point]; s -> a; a [label=A]; b [label=B]; c [label=C];"
                               " a -> b; a -> c [label=y]; }"))
        self.assertIn("IF (no condition)", out)
        out = pseudocode(parse('digraph m { s [shape=point]; s -> a; a [label=A]; b [label=B]; a -> b [check="test -f x"]; }'))
        self.assertIn("IF ⚙ test -f x  GOTO @b", out)
        self.assertNotIn("(no condition)", out)
        out = pseudocode(parse('digraph g { s [shape=point]; s -> a; a; b [label="say \\"hi\\""]; a -> b; }'))
        self.assertIn("\n  @a:\n", out + "\n")
        self.assertNotIn('"', next(l for l in out.splitlines() if l.strip().startswith("@a:")))
        self.assertIn('@b:  "say "hi""', out)
        self.assertNotIn("unreachable:", out)


if __name__ == "__main__":
    unittest.main()
