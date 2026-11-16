from __future__ import annotations

import unittest
from pathlib import Path

from smlib.dot import parse


def read(name):
    return (Path(__file__).parent / "fixtures" / name).read_text()


class DotTests(unittest.TestCase):
    def test_three_shapes_parse(self):
        g = parse(read("loop.dot"))
        self.assertEqual(g.name, "fix_bug")
        self.assertEqual(g.states(), ["reproduce", "diagnose", "fix", "verify", "done"])
        self.assertEqual(g.initial(), "reproduce")
        self.assertTrue(g.is_final("done"))
        self.assertEqual([e["dst"] for e in g.exits("verify")], ["done", "fix"])
        self.assertEqual(g.exits("verify")[0]["check"], "npm test")
        self.assertEqual(parse(read("chain.dot")).states(), ["a", "b", "c"])
        self.assertEqual(parse(read("cycle.dot")).exits("act")[0]["dst"], "poll")

    def test_check_string_survives(self):
        src = 'digraph g { a -> b [check="grep -q \'#todo\' f && echo \\"ok\\" | cat; true // x"]; }'
        self.assertEqual(parse(src).edges[0]["check"], 'grep -q \'#todo\' f && echo "ok" | cat; true // x')

    def test_comments_and_ignored_attributes(self):
        src = 'digraph g {\n  // c\n  # c\n  /* c */ rankdir=LR; node [shape=box];\n  a [label="A\\nB", color=red];\n}'
        g = parse(src)
        self.assertEqual((g.states(), g.label("a")), (["a"], "A B"))

    def test_hand_edited_layout(self):  # Review Focus 1
        g = parse("digraph g {\r\n\ta [label=x]\r\n\tb [label=y]\r\n\ta -> b\r\n}\r\n")
        self.assertEqual((g.states(), g.edges[0]["dst"]), (["a", "b"], "b"))

    def test_outside_subset_is_rejected_with_line(self):
        for bad in ["digraph g {\n subgraph s { a; }\n}", "digraph g {\n a -- b;\n}",
                    "digraph g {\n a -> b -> c;\n}", 'digraph g {\n a [label="x\ny"];\n}',
                    "digraph g {\n a:n -> b;\n}", "digraph g {\n a [label=<b>];\n}"]:
            with self.assertRaisesRegex(ValueError, r"^line 2: "):
                parse(bad)
        with self.assertRaisesRegex(ValueError, r"^line 1: "):
            parse("graph g {\n a -- b;\n}")

    def test_bad_state_ids_are_rejected(self):  # Review Focus 2
        for bad in ["digraph g {\n run-tests [label=x];\n}", 'digraph g {\n "fix bug" [label=x];\n}']:
            with self.assertRaisesRegex(ValueError, r"line 2: .*letters, digits and underscores"):
                parse(bad)

    def test_check_as_a_default_is_rejected(self):
        with self.assertRaisesRegex(ValueError, r"^line 2: check is not supported as a default"):
            parse('digraph g {\n edge [check="true"];\n a -> b;\n}')
        g = parse('digraph g { node [fontname="Helvetica"]; edge [color=gray]; a -> b; }')
        self.assertEqual(g.edges[0]["dst"], "b")

    def test_graph_attributes_in_both_forms(self):
        g = parse('digraph g { graph [on_finish="a b", rankdir=LR]; on_move="c"; node [color=red]; a -> b; }')
        self.assertEqual(g.attrs, {"on_finish": "a b", "rankdir": "LR", "on_move": "c"})
        self.assertEqual(parse("digraph g { a -> b; }").attrs, {})

    def test_ask_is_parsed_and_defaults_cannot_carry_it_or_hooks(self):
        g = parse('digraph g { a -> b [ask="Ship it?"]; b -> c; c -> d [ask=""]; }')
        self.assertEqual([(e["ask"], e["ask_set"]) for e in g.edges], [("Ship it?", True), ("", False), ("", True)])
        for bad, word in (('edge [ask="x"]', "ask"), ('node [on_move="x"]', "on_move"), ('edge [on_var="x"]', "on_var")):
            with self.assertRaisesRegex(ValueError, rf"^line 2: {word} is not supported"):
                parse(f"digraph g {{\n {bad};\n a -> b;\n}}")
