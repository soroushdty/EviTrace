"""
tests/steering/test_spec_task_traceability.py
=============================================
Mechanical check that every ticked task in an active Kiro spec is backed by git
history, and that each task's traceability annotations resolve.

The per-task reviewer and ``/kiro-validate-impl`` judge whether work is *good*;
this file checks, without judgment, that a ``[x]`` is a claim with a commit
behind it. The convention it enforces is documented in
``.kiro/steering/testing.md`` ("Spec-Task Traceability"):

* **One tick per commit, named in the subject.** A commit that flips a leaf task
  from ``[ ]`` to ``[x]`` in ``.kiro/specs/<spec>/tasks.md`` has the subject
  ``<type>(<spec>): <task-id> <description>``.
* **Bookkeeping ticks cite their evidence.** A tick commit that changes nothing
  outside ``.kiro/specs/<spec>/`` must say ``delivered in <task-ids>``, and each
  cited task must have been ticked earlier by a commit that did change files
  outside the spec directory.
* **No pre-ticked boxes.** A ``tasks.md`` created with ``[x]`` boxes counts as
  ticking them in its creation commit, so the subject rule above rejects it.
* **Annotations resolve** (current tree): every leaf task has ``_Requirements:_``
  whose ids exist in the spec's ``requirements.md``; ``(P)`` tasks carry
  ``_Boundary:_``; ``_Depends:_`` ids exist; a ticked group task has no open
  sub-task.

Scope: spec directories directly under ``.kiro/specs/`` (never ``archive/``).
History checks skip when git or full history is unavailable (no ``.git``,
shallow clone).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SPECS_DIR = PROJECT_ROOT / ".kiro" / "specs"

_TASK_LINE = re.compile(r"^- \[(?P<mark>[ x])\]\*?\s+(?P<id>\d+(?:\.\d+)*)\.?\s+(?P<title>.*)$")
_ANNOTATION = re.compile(r"_(?P<kind>Requirements|Depends|Boundary):\s*(?P<value>.+?)_\s*$")
_REQUIREMENT_HEADING = re.compile(r"^#{3,4}\s+Requirement\s+(?P<num>\d+)\b")
_TOP_HEADING = re.compile(r"^#{1,2}\s")
_CRITERION_LINE = re.compile(r"^(?P<num>\d+)\.\s")
_SUBJECT = re.compile(r"^[a-z]+\((?P<spec>[a-z0-9][a-z0-9-]*)\): (?P<id>\d+(?:\.\d+)*)(?=\s|$)")
_DELIVERED_IN = re.compile(r"delivered in (?P<rest>[^)]*)", re.IGNORECASE)
_TASK_ID = re.compile(r"\d+(?:\.\d+)*")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass
class Task:
    id: str
    done: bool
    title: str
    annotations: dict[str, list[str]] = field(default_factory=dict)


def parse_tasks(text: str) -> dict[str, Task]:
    """Return tasks keyed by id, in file order. A task's body ends at the next
    task line or Markdown heading."""
    tasks: dict[str, Task] = {}
    current: Task | None = None
    for line in text.splitlines():
        match = _TASK_LINE.match(line)
        if match:
            current = Task(match["id"], match["mark"] == "x", match["title"].strip())
            tasks[current.id] = current
            continue
        if line.startswith("#"):
            current = None
            continue
        if current is None:
            continue
        annotation = _ANNOTATION.search(line)
        if annotation:
            values = [v.strip() for v in annotation["value"].split(",") if v.strip()]
            current.annotations.setdefault(annotation["kind"], []).extend(values)
    return tasks


def leaf_ids(tasks: dict[str, Task]) -> set[str]:
    """Tasks with no sub-task. A group task (``1.``) with sub-tasks (``1.1``) is a
    header, not a work item; a group task without sub-tasks is a work item."""
    return {tid for tid in tasks if not any(other.startswith(tid + ".") for other in tasks)}


def done_leaves(text: str | None) -> set[str]:
    if text is None:
        return set()
    tasks = parse_tasks(text)
    return {tid for tid in leaf_ids(tasks) if tasks[tid].done}


def requirement_ids(text: str) -> set[str]:
    """``N`` for each ``### Requirement N`` heading and ``N.M`` for each numbered
    criterion under it. A level-1/2 heading ends the requirements section."""
    ids: set[str] = set()
    current: str | None = None
    for line in text.splitlines():
        heading = _REQUIREMENT_HEADING.match(line)
        if heading:
            current = heading["num"]
            ids.add(current)
            continue
        if _TOP_HEADING.match(line):
            current = None
            continue
        criterion = _CRITERION_LINE.match(line)
        if criterion and current is not None:
            ids.add(f"{current}.{criterion['num']}")
    return ids


def delivered_in(subject: str) -> list[str]:
    match = _DELIVERED_IN.search(subject)
    return _TASK_ID.findall(match["rest"]) if match else []


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def static_violations(tasks_text: str, requirements_text: str) -> list[str]:
    tasks = parse_tasks(tasks_text)
    known_requirements = requirement_ids(requirements_text)
    leaves = leaf_ids(tasks)
    violations: list[str] = []
    for tid, task in tasks.items():
        if tid in leaves:
            refs = task.annotations.get("Requirements", [])
            if not refs:
                violations.append(f"task {tid} has no _Requirements:_ annotation")
            for ref in refs:
                if ref not in known_requirements:
                    violations.append(f"task {tid} cites requirement {ref!r}, which requirements.md does not define")
        elif task.done:
            open_children = sorted(o for o in tasks if o.startswith(tid + ".") and not tasks[o].done)
            if open_children:
                violations.append(f"group task {tid} is ticked but sub-tasks {open_children} are open")
        if "(P)" in task.title and not task.annotations.get("Boundary"):
            violations.append(f"task {tid} is marked (P) but has no _Boundary:_ annotation")
        for dep in task.annotations.get("Depends", []):
            if dep not in tasks:
                violations.append(f"task {tid} depends on {dep!r}, which is not a task in this file")
    return violations


@dataclass
class TickCommit:
    sha: str
    subject: str
    files: list[str]
    ticked: set[str]


def tick_violations(spec: str, commits: list[TickCommit]) -> list[str]:
    """Check tick commits of one spec, oldest first."""
    spec_prefix = f".kiro/specs/{spec}/"
    ticked_by: dict[str, TickCommit] = {}
    violations: list[str] = []

    def bookkeeping_only(commit: TickCommit) -> bool:
        return all(path.startswith(spec_prefix) for path in commit.files)

    for commit in commits:
        if not commit.ticked:
            continue
        where = f"{commit.sha[:9]} {commit.subject!r}"
        subject = _SUBJECT.match(commit.subject)
        for tid in sorted(commit.ticked):
            if subject is None or subject["spec"] != spec or subject["id"] != tid:
                violations.append(f"{where} ticks {spec} {tid}; subject must start with '<type>({spec}): {tid} '")
        if bookkeeping_only(commit):
            cited = delivered_in(commit.subject)
            if not cited:
                violations.append(
                    f"{where} changes nothing outside {spec_prefix} and cites no 'delivered in <task-ids>'"
                )
            for tid in cited:
                source = ticked_by.get(tid)
                if source is None:
                    violations.append(f"{where} cites delivered in {tid}, which no earlier commit ticked")
                elif bookkeeping_only(source):
                    violations.append(
                        f"{where} cites delivered in {tid}, but {source.sha[:9]} also changed nothing outside {spec_prefix}"
                    )
        for tid in commit.ticked:
            ticked_by.setdefault(tid, commit)
    return violations


# ---------------------------------------------------------------------------
# Git access
# ---------------------------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True)


def _history_unavailable_reason() -> str | None:
    if shutil.which("git") is None:
        return "git executable not found"
    if not (PROJECT_ROOT / ".git").exists():
        return "not a git checkout"
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() != "false":
        return "shallow clone: history incomplete"
    return None


def _file_at(rev: str, path: str) -> str | None:
    result = _git("show", f"{rev}:{path}")
    return result.stdout if result.returncode == 0 else None


def tick_commits(spec: str) -> list[TickCommit]:
    path = f".kiro/specs/{spec}/tasks.md"
    log = _git("log", "--no-merges", "--reverse", "--format=%H%x1f%s", "--", path)
    commits: list[TickCommit] = []
    for line in log.stdout.splitlines():
        sha, subject = line.split("\x1f", 1)
        ticked = done_leaves(_file_at(sha, path)) - done_leaves(_file_at(f"{sha}^", path))
        files = _git("show", "--name-only", "--format=", sha).stdout.split()
        commits.append(TickCommit(sha, subject, files, ticked))
    return commits


def _active_specs() -> list[str]:
    if not SPECS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in SPECS_DIR.iterdir() if p.is_dir() and p.name != "archive" and (p / "tasks.md").is_file()
    )


# ---------------------------------------------------------------------------
# Rule unit tests (synthetic input)
# ---------------------------------------------------------------------------

_SAMPLE_TASKS = """\
# Implementation Plan

- [ ] 1. Foundation
- [x] 1.1 Add config keys
  - Observable completion: a test asserts the defaults
  - _Requirements: 1.1, 1.2_
- [ ]* 1.2 (P) Optional baseline tests
  - _Requirements: 2_
  - _Boundary: Config_
- [x] 2. Standalone work item
  - _Requirements: 2.1_
  - _Depends: 1.1_
"""

_SAMPLE_REQUIREMENTS = """\
# Requirements

### Requirement 1: Config
#### Acceptance Criteria
1. first
2. second

### Requirement 2: Other
1. only

## Appendix
3. numbered appendix line, not a criterion
"""


def test_parse_tasks_reads_marks_ids_optional_and_annotations():
    tasks = parse_tasks(_SAMPLE_TASKS)
    assert list(tasks) == ["1", "1.1", "1.2", "2"]
    assert tasks["1.1"].done and not tasks["1.2"].done
    assert tasks["1.2"].title.startswith("(P)")
    assert tasks["1.1"].annotations == {"Requirements": ["1.1", "1.2"]}
    assert tasks["2"].annotations == {"Requirements": ["2.1"], "Depends": ["1.1"]}


def test_group_task_with_subtasks_is_not_a_leaf_but_standalone_task_is():
    assert leaf_ids(parse_tasks(_SAMPLE_TASKS)) == {"1.1", "1.2", "2"}
    assert done_leaves(_SAMPLE_TASKS) == {"1.1", "2"}
    assert done_leaves(None) == set()


def test_requirement_ids_cover_headings_and_criteria_but_stop_at_top_heading():
    assert requirement_ids(_SAMPLE_REQUIREMENTS) == {"1", "1.1", "1.2", "2", "2.1"}


def test_static_violations_flags_each_rule():
    tasks = """\
- [x] 1. Group
- [x] 1.1 Done child
  - _Requirements: 1.1_
- [ ] 1.2 (P) Open child without boundary
  - _Requirements: 7.7_
  - _Depends: 4.2_
- [ ] 2. No requirements
"""
    violations = static_violations(tasks, _SAMPLE_REQUIREMENTS)
    assert any("group task 1 is ticked" in v and "1.2" in v for v in violations)
    assert any("task 1.2 cites requirement '7.7'" in v for v in violations)
    assert any("task 1.2 is marked (P)" in v for v in violations)
    assert any("task 1.2 depends on '4.2'" in v for v in violations)
    assert any("task 2 has no _Requirements:_" in v for v in violations)
    assert len(violations) == 5


def test_static_violations_accepts_a_well_formed_file():
    assert static_violations(_SAMPLE_TASKS, _SAMPLE_REQUIREMENTS) == []


def _commit(sha: str, subject: str, files: list[str], ticked: set[str]) -> TickCommit:
    return TickCommit(sha * 8, subject, files, ticked)


_TASKS_MD = ".kiro/specs/demo/tasks.md"


def test_tick_commit_subject_must_name_spec_and_task():
    ok = _commit("a", "feat(demo): 1.1 add keys", [_TASKS_MD, "src/x.py"], {"1.1"})
    wrong_id = _commit("b", "feat(demo): 1.3 add keys", [_TASKS_MD, "src/x.py"], {"1.2"})
    wrong_spec = _commit("c", "feat(other): 2.1 add keys", [_TASKS_MD, "src/x.py"], {"2.1"})
    no_id = _commit("d", "feat(demo): add keys", [_TASKS_MD, "src/x.py"], {"3.1"})
    prefix_only = _commit("e", "feat(demo): 4.10 add keys", [_TASKS_MD, "src/x.py"], {"4.1"})
    violations = tick_violations("demo", [ok, wrong_id, wrong_spec, no_id, prefix_only])
    assert [v.split()[0] for v in violations] == ["bbbbbbbb", "cccccccc", "dddddddd", "eeeeeeee"]


def test_two_ticks_in_one_commit_cannot_both_be_named():
    both = _commit("a", "feat(demo): 1.1 two at once", [_TASKS_MD, "src/x.py"], {"1.1", "1.2"})
    violations = tick_violations("demo", [both])
    assert len(violations) == 1 and "1.2" in violations[0]


def test_bookkeeping_tick_must_cite_real_earlier_work():
    real = _commit("a", "feat(demo): 1.1 real work", [_TASKS_MD, "tests/t.py"], {"1.1"})
    cited_ok = _commit("b", "chore(demo): 1.2 mark complete (delivered in 1.1)", [_TASKS_MD], {"1.2"})
    uncited = _commit("c", "chore(demo): 1.3 mark complete", [_TASKS_MD, ".kiro/specs/demo/spec.json"], {"1.3"})
    cites_future = _commit("d", "chore(demo): 1.4 mark complete (delivered in 1.9)", [_TASKS_MD], {"1.4"})
    cites_bookkeeping = _commit("e", "chore(demo): 1.5 mark complete (delivered in 1.1 and 1.2)", [_TASKS_MD], {"1.5"})
    violations = tick_violations("demo", [real, cited_ok, uncited, cites_future, cites_bookkeeping])
    assert [v.split()[0] for v in violations] == ["cccccccc", "dddddddd", "eeeeeeee"]
    assert "1.2" in violations[2] and "bbbbbbbb" in violations[2]


def test_delivered_in_extracts_ids_up_to_the_closing_parenthesis():
    assert delivered_in("chore(x): 2.2 mark tests complete (delivered in 2.1, verified against pre-change tree)") == ["2.1"]
    assert delivered_in("chore(x): 6.3 mark complete (delivered in 6.1 and 6.2)") == ["6.1", "6.2"]
    assert delivered_in("feat(x): 1.1 no citation") == []


def test_commits_without_ticks_are_ignored():
    edit = _commit("a", "docs: reword tasks", [_TASKS_MD], set())
    assert tick_violations("demo", [edit]) == []


# ---------------------------------------------------------------------------
# The real specs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _active_specs())
def test_active_spec_annotations_resolve(spec):
    spec_dir = SPECS_DIR / spec
    requirements = spec_dir / "requirements.md"
    assert requirements.is_file(), f"{spec}: tasks.md exists but requirements.md does not"
    violations = static_violations(
        (spec_dir / "tasks.md").read_text(encoding="utf-8"), requirements.read_text(encoding="utf-8")
    )
    assert not violations, f"{spec}:\n  " + "\n  ".join(violations)


@pytest.mark.parametrize("spec", _active_specs())
def test_active_spec_ticks_are_backed_by_commits(spec):
    reason = _history_unavailable_reason()
    if reason:
        pytest.skip(reason)
    violations = tick_violations(spec, tick_commits(spec))
    assert not violations, f"{spec}:\n  " + "\n  ".join(violations)
