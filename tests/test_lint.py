from __future__ import annotations

import unittest
from pathlib import Path

from smlib.dot import parse
from smlib.lint import lint


def read(name):
    return (Path(__file__).parent / "fixtures" / name).read_text()


class LintTests(unittest.TestCase):
    def test_fixtures_are_clean(self):
        for name in ("chain.dot", "loop.dot"):
            self.assertEqual(lint(parse(read(name))), ([], []))

    def test_endless_cycle_is_valid_with_note(self):
        self.assertEqual(lint(parse(read("cycle.dot"))), ([], ["no final state: runs until stopped"]))

    def test_each_rule_fires(self):
        cases = {
            "dead end": 'digraph g { s [shape=point]; s -> a; a [label=x]; b [label=y]; a -> b; }',
            "final state with an exit": 'digraph g { s [shape=point]; s -> a; a [label=x, shape=doublecircle]; a -> a; }',
            "unreachable": 'digraph g { s [shape=point]; s -> a; a [label=x, shape=doublecircle]; b [label=y]; b -> a; }',
            "no label": 'digraph g { s [shape=point]; s -> a; a [shape=doublecircle]; }',
            "undeclared": 'digraph g { s [shape=point]; s -> a; a [label=x]; a -> zz; }',
            "duplicate edge": 'digraph g { s [shape=point]; s -> a; a [label=x]; a -> a; a -> a; }',
            "entry": 'digraph g { a [label=x]; a -> a; }',
            "no condition": 'digraph g { s [shape=point]; s -> a; a [label=x]; b [label=y, shape=doublecircle]; a -> a; a -> b [label=ok]; }',
        }
        for word, src in cases.items():
            errors, _ = lint(parse(src))
            self.assertTrue(any(word in e for e in errors), (word, errors))
