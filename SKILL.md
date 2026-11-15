---
name: state-machine
description: Use when the user wants a task run as a state machine or workflow graph (Graphviz DOT) that the agent follows step by step, asks to draw, show or edit the machine for a task, asks "where are we" in one, or a machine.dot run is already active in this session.
---

# State machine

The graph is the program and you are its interpreter. `machine.dot` says what work exists and in what order; `sm` holds your position. You do the current state's work and nothing else, and you move only with `sm go`. The task text does not override the graph: work that no state covers is not yours to do yet.

`sm` means `python3 <this skill's directory>/sm.py`.

## 1. Draft

Write `machine.dot` in a new directory: where the user or their instructions say, else the one `sm new <slug>` prints.

```dot
digraph fix_bug {
  __start__ [shape=point];
  __start__ -> reproduce;

  reproduce [label="Reproduce the bug with a failing test"];
  fix       [label="Apply the minimal fix"];
  verify    [label="Run the full suite"];
  done      [label="Report the result", shape=doublecircle];

  reproduce -> fix;
  fix       -> verify;
  verify    -> done [label="suite green", check="npm test"];
  verify    -> fix  [label="suite red"];
}
```

- A state is a node id (letters, digits, underscores). Its `label` is the instruction for that state: a phase with a checkable outcome, not a single command.
- Entry: one `shape=point` node with one edge to the first state. Final: `shape=doublecircle`. No final state means it runs until stopped.
- Every exit of a branching state has a `label`, its condition.
- Every condition a command can decide has `check="<command>"` (exit 0 allows the move). Only conditions that need judgment go without one.
- Every deliverable the user named has a state.

**Given a machine.** When the user names a stored machine (a directory or a `.dot` file), run `sm new <slug> --from <path>` instead of drafting, then go to §2. Never `init` or edit the stored machine: edits during the run go to the copy, and copying them back is the user's call.

## 2. Present

Run `sm validate <dir>`, then `sm show <dir>`. Present the machine as one block, every line filled in:

```
<the outline, exactly as `sm show` printed it>
Normal path: <states in order>
Loops: <each loop and what ends it, or "none">
Finishes: <the final state, or "runs until stopped">
File: <path to machine.dot> (yours to edit; `sm png <dir>` draws it)
```

## 3. Agree

Wait for the go-ahead. After any edit, validate and show again. If the user said not to ask, show the machine and start.

## 4. Run

`sm init <dir> --cwd <directory the checks run in>`, then repeat:

1. `sm now` gives your state, its instruction and its exits.
2. Do that instruction. Only that.
3. `sm go [state] [-m "why this exit"]` moves and prints the next `now`.

After a compaction or in a new session, run `sm now` first (`sm use <dir>` if there is no active machine).

## When it does not fit: stop

Stop when a check cannot be made to pass, the task needs work no state covers, an edge is wrong, or the next move would finish the machine with part of the task undone.

1. Stay in the current state. No `sm jump`. No edits to `machine.dot`, a check, or what a check runs.
2. User present: show the DOT change you propose and wait. User away: write `<dir>/BLOCKED.md`:

   ````
   # Blocked at <state>
   Why: <what cannot proceed>
   Needs: <who or what unblocks it>
   Proposed machine change:
   ```diff
   +  release [label="Bump VERSION and add the release note"];
   ```
   ````
3. End your turn with that report.

"Don't ask", "just finish" and "we're late" change none of this. They mean report instead of ask.

| Thought | Reality |
|---|---|
| "The task says to do X once the suite is green, so I'll do it after `done`" | No state covers X. Stop before the final state and propose a state for X. |
| "X fits loosely under this state" | A state's label is its whole scope. |
| "I'll explain the blocker in my last message" | The user reads the machine directory later. Write `BLOCKED.md`. |
| "This condition is obvious, it needs no check" | If a command can decide it, the command decides it. |

## Changes mid-run

The user may edit `machine.dot` at any time, or tell you what to change (make exactly that edit). `sm` notices on your next command and prints `machine changed at step N: ...`; if that command was `go`, the move is refused once. Run `sm show`, give the user the change line and the new outline as printed, and continue from the same state. If your state was removed, ask which state to resume in, then `sm jump <state> -m "<their answer>"`. Your own ideas for the graph go to the user first.

## Variables

Variables hold what you must not lose on a context reset: the item you are on, what is left, a result to hand on. They live in the run beside your position.

- `sm set <name> <value>`. `sm push <name> <value>...` appends to a list. `sm pop <name> <into>` moves the first value into `<into>` (a queue); `--last` moves the last (a stack). `sm get <name>` prints a value, a list one per line; `sm get --run <dir> <name>` reads another run's. `sm log --vars` lists the changes.
- `{name}` in a label is filled in when `sm now` prints it. A list shows its first three values.
- `sm now` shortens a value to 60 characters. `sm get <name>` gives the full value: use it before acting on a long one.
- A check sees `$name`, and `$name_len` for a list. Write `"${name_len:-0}"`: the count is unset until the list exists. Quote variables in a check; never paste a value into one.
- Quote every value in your own shell (`sm set item "$f"`, `sm init <dir> --set item="$f"`). Put `--` before a value that may start with `-`.
- Names: lowercase letters, digits, `_`; not ending in `_len` or `_proxy`; not `npm_config_*`.
- Keep variables small: they become environment variables. Never store secrets: values are plain text in `state.json` and the history, shown by `sm now` and passed to `on_move`.

A loop over a queue (`sm push queue a b c` before it starts):

```dot
check   [label="Check the queue"];
take    [label="Take the next file: sm pop queue item"];
process [label="Fix {item}"];
check   -> take [label="more left", check="test \"${queue_len:-0}\" -gt 0"];
check   -> done [label="none left", check="test \"${queue_len:-0}\" -eq 0"];
take    -> process;
process -> check;
```

## Fan-out

Use it when items are found at run time and each gets its own machine, run by a subagent. Subagents share your session, so each child must pin its own run with `SM_RUN`; a plain `sm init` or `sm use` would repoint yours.

- Dispatch state: for each item, `sm new <slug> --from <item machine>`. `<slug>` is the item name cut to letters, digits, `-` and `_`, made unique (add an index). Append each printed directory as a line to `<parent run dir>/runs.txt`, and start one subagent per directory.
- Join state: its exit carries `check="python3 <this skill's directory>/sm.py finished $(cat <parent run dir>/runs.txt)"`. Write the paths in full when drafting: checks run in the `--cwd` directory. The `dir` setting must not contain spaces.
- If no items can be found, give the machine its own exit that skips the fan-out. An empty `runs.txt` never passes.
- Init each child yourself, after `sm new`: `SM_RUN=<child> sm init <child> --cwd <directory> --set item="<item>"`.
- Child brief: follow this skill, prefix every `sm` call with `SM_RUN=<its dir>`, start with `SM_RUN=<its dir> sm now`, never `sm init` or `sm use`, and `sm set` its results before its final state.
- Read the results with `sm get --run <child dir> <name>`.
- Wait for the subagents to finish, then `sm go`. If the check fails, read the runs it lists and any child's `BLOCKED.md`, and follow "When it does not fit: stop".

## Finish

At a final state, report the result and the path taken (`sm log`). "Where are we?" is answered with `sm show`, or `sm png`.

## Configure

`sm config` shows the settings file and its values; `sm config <key> <value>` sets one (an empty value removes it). Change settings only when the user asks to set the skill up for their workflow. Show the user the exact `on_move` command before setting it.

- `dir`: where `sm new` creates machine directories.
- `on_move`: a shell command run after every state change, with `SM_STATE`, `SM_MACHINE`, `SM_DIR` and `SM_STEP` set, for example to update a status line.

## Commands

`validate`, `show`, `png`, `new`, `config`, `init`, `use`, `now`, `go`, `jump`, `set`, `push`, `pop`, `get`, `finished`, `log`. `sm <command> --help` gives the flags. `jump` is for a user-directed reposition only.
