"""``select_tests.py`` — tier-scoped pytest plans for SDD agents (FEAT-563, querysource port).

Same CLI contract as ai-parrot's ``scripts/sdd/select_tests.py``, adapted to
querysource's single-package layout (``querysource/<subsystem>/...`` with a
mostly flat, partly mirrored ``tests/`` tree) instead of ai-parrot's
``packages/<dist>/`` monorepo kernel:

* ``task``    — the task's declared ``## Validation Commands`` ∪ mirror.
* ``merge``   — mirror ∪ core escalation.
* ``feature`` — declared ∪ mirror ∪ core escalation.

*Mirror* maps each changed file to the deepest existing ``tests/<subdirs>``
directory plus any ``test_*<stem>*.py`` module that names it. *Core
escalation* runs the whole ``tests/`` suite (still excluding ``perf``) when a
module listed in ``CORE_MODULES`` changes. Broad declared pytest commands
(no path, ``.`` or ``tests``) are dropped with a note — CI owns full runs.

Usage:
    python -m scripts.sdd.select_tests --tier {task,merge,feature} [--base origin/dev]
        [--task-file sdd/tasks/active/TASK-NNN-x.md ...] [--worktree .] [--run] [--json]
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Sequence

try:
    from scripts.sdd import _validation_contract as contract
except ImportError:  # loaded as a sibling script by worktree_environment.py
    import _validation_contract as contract  # type: ignore[no-redef]

TIERS: tuple[str, ...] = ("task", "merge", "feature")
AGENT_FLAGS: tuple[str, ...] = ("-q", "--tb=short", "-p", "no:cacheprovider", "-o", "log_cli=false")
MARKER_EXPRESSION: str = "not perf"
SUITE: str = "tests"
PACKAGE: str = "querysource"

#: High fan-in modules whose change escalates merge/feature tiers to the whole suite.
CORE_MODULES: frozenset[str] = frozenset(
    {
        "querysource/conf.py",
        "querysource/connections.py",
        "querysource/exceptions.py",
        "querysource/models.py",
        "querysource/queries/qs.py",
        "querysource/queries/multi/registry.py",
        "querysource/providers/abstract.py",
        "querysource/providers/external.py",
        "querysource/parsers/abstract.pyx",
        "querysource/parsers/abstract.pxd",
        "querysource/outputs/output.py",
    }
)

BLOCK_MESSAGE: str = (
    "no scoped tests for this attempt — add test paths to the task's `## Validation Commands` "
    "or run a specific test file"
)

_SOURCE_SUFFIXES = (".py", ".pyx", ".pxd")


@dataclass(frozen=True)
class TestTarget:
    """One pytest operand selected for a plan."""

    __test__ = False  # not a pytest test class

    path: str
    reason: str  # "declared" | "mirror" | "core"


@dataclass
class ScopePlan:
    """The per-tier selection result (one invocation at most: single package)."""

    tier: str
    argv: list[str] = field(default_factory=list)
    targets: list[TestTarget] = field(default_factory=list)
    escalated: bool = False
    core_hits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _declared_targets(declared: Sequence[Sequence[str]], worktree: Path, notes: list[str]) -> list[str]:
    """Narrow pytest operands of the declared commands; broad ones are dropped with a note."""
    paths: list[str] = []
    for argv in declared:
        operands = contract._pytest_operands(argv)
        if operands is None:
            continue
        if contract.is_broad_pytest(argv, worktree=worktree):
            notes.append(f"dropped broad declared pytest: {shlex.join(argv)}")
            continue
        for op in operands:
            if (worktree / op.split("::", 1)[0]).exists():
                paths.append(op)
            else:
                notes.append(f"declared target does not exist, skipped: {op}")
    return paths


def mirror_targets(path: str, worktree: Path) -> list[str]:
    """Map one changed file to its narrowest existing test targets.

    Args:
        path: Repo-relative changed path.
        worktree: Root the path is relative to.

    Returns:
        Existing test paths (may be empty).
    """
    parts = PurePosixPath(path).parts
    if not parts:
        return []
    if parts[0] == SUITE:
        return [path] if (worktree / path).exists() and path.endswith(".py") else []
    if parts[0] != PACKAGE or not path.endswith(_SOURCE_SUFFIXES):
        return []
    stem = PurePosixPath(path).stem
    subdirs = parts[1:-1]
    targets: list[str] = []
    for depth in range(len(subdirs), 0, -1):
        candidate = "/".join((SUITE, *subdirs[:depth]))
        if (worktree / candidate).is_dir():
            targets.append(candidate)
            break
    if stem not in ("__init__", "abstract", "base"):
        for test in sorted((worktree / SUITE).rglob(f"test_*{stem}*.py")):
            targets.append(test.relative_to(worktree).as_posix())
    return targets


def prune_nested(paths: Sequence[str]) -> list[str]:
    """Drop targets already covered by a broader target; sorted and deduped."""
    unique = set(paths)
    return sorted(t for t in unique if not any(t.startswith(f"{o}/") for o in unique if o != t))


def changed_files(worktree: Path, base_ref: str) -> list[str]:
    """``git diff --name-only --diff-filter=d <base>...HEAD`` ∪ uncommitted/untracked paths."""
    files: list[str] = []
    runs = (
        ["git", "diff", "--name-only", "--diff-filter=d", f"{base_ref}...HEAD"],
        ["git", "status", "--porcelain", "--untracked-files=all"],
    )
    for index, cmd in enumerate(runs):
        try:
            out = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True, check=False)
        except OSError:
            continue
        if out.returncode != 0:
            continue
        for line in out.stdout.splitlines():
            if index == 1:
                if len(line) < 4 or "D" in line[:2]:
                    continue
                line = line[3:].split(" -> ", 1)[-1]
            line = line.strip()
            if line and line not in files:
                files.append(line)
    return files


def plan_tests(
    worktree: Path, changed: Sequence[str], tier: str, declared: Sequence[Sequence[str]] = ()
) -> ScopePlan:
    """Build the tier's plan: see module docstring for the per-tier union."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {TIERS}")
    plan = ScopePlan(tier=tier)
    targets: list[TestTarget] = []
    if tier in ("task", "feature"):
        targets += [TestTarget(p, "declared") for p in _declared_targets(declared, worktree, plan.notes)]
    for path in changed:
        targets += [TestTarget(p, "mirror") for p in mirror_targets(path, worktree)]
    if tier != "task":
        plan.core_hits = sorted(p for p in changed if p in CORE_MODULES)
        if plan.core_hits and (worktree / SUITE).is_dir():
            plan.escalated = True
            targets.append(TestTarget(SUITE, "core"))
    kept = prune_nested([t.path for t in targets])
    priority = {"declared": 0, "core": 1, "mirror": 2}
    best: dict[str, TestTarget] = {}
    for target in targets:
        if target.path in kept and (
            target.path not in best or priority[target.reason] < priority[best[target.path].reason]
        ):
            best[target.path] = target
    plan.targets = [best[p] for p in kept if p in best]
    if plan.targets:
        plan.argv = [
            "pytest",
            *AGENT_FLAGS,
            "-m",
            MARKER_EXPRESSION,
            "--confcutdir",
            str(worktree),
            *[t.path for t in plan.targets],
        ]
    return plan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan (and optionally run) tier-scoped pytest invocations.")
    parser.add_argument("--tier", required=True, choices=TIERS)
    parser.add_argument("--base", default="origin/dev")
    parser.add_argument("--task-file", action="append", default=[], type=Path)
    parser.add_argument("--worktree", type=Path, default=Path.cwd())
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: 0 ok, 1 the invocation failed, 2 usage error / empty task-tier plan."""
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit:
        return 2
    worktree = args.worktree.resolve()
    declared: list[list[str]] = []
    for task_file in args.task_file:
        path = task_file if task_file.is_absolute() else worktree / task_file
        try:
            declared.extend(contract.parse_validation_commands(path.read_text(encoding="utf-8")))
        except OSError:
            continue

    plan = plan_tests(worktree, changed_files(worktree, args.base), args.tier, declared)
    out = sys.stdout
    if not plan.argv and args.tier == "task":
        out.write(f"{BLOCK_MESSAGE}\n")
        return 2
    if args.json:
        payload = asdict(plan)
        out.write(json.dumps(payload, indent=2) + "\n")
    else:
        if plan.argv:
            out.write(shlex.join(plan.argv) + "\n")
        for note in plan.notes:
            out.write(f"# note: {note}\n")
        if plan.escalated:
            out.write(f"# escalated: {PACKAGE} (core: {', '.join(plan.core_hits)})\n")
    if not args.run or not plan.argv:
        return 0
    result = subprocess.run(plan.argv, cwd=worktree, check=False)
    return 0 if result.returncode in (0, 5) else 1


if __name__ == "__main__":
    raise SystemExit(main())
