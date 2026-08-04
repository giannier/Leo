"""Enforce the Constitution's coverage standard, module by module.

The standard is not one number: `constitution.md` asks for 80% on the critical
modules - agent-core, tools, channels, billing - and 70% on the rest. A single
global `fail_under` cannot express that. It would let the agent slide to 40%
while a well-tested repository layer carried the average, which is precisely
backwards: the repositories fail loudly, and a wrong answer from the agent is
sent to a customer over WhatsApp.

So the groups are declared here and checked separately. Run it with
`make coverage`, after `coverage run`; CI runs the same target.

Coverage is line **and** branch: an `if` whose false arm nobody has ever taken
is not covered code, and counting it as such is how a gate ends up green over
an untested error path.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import coverage

# `constitution.md`, "Estandares de Calidad".
CRITICAL_MINIMUM = 80.0
BASELINE_MINIMUM = 70.0

# Path prefixes, relative to `backend/`. A file matching none of these falls
# into "the rest" and is held to the baseline.
#
# `channels` has one member today - the WhatsApp adapter - because the channel
# itself arrives with Feature 002. It is listed now so the gate is already in
# place when that code lands, rather than being remembered afterwards.
CRITICAL_GROUPS: dict[str, tuple[str, ...]] = {
    "agent-core and tools": ("app/agent/",),
    "channels": ("app/providers/whatsapp/",),
    # No `services/billing.py` yet (Feature 009). What handles money today is
    # the trial's metering and quota enforcement, and the payments repository.
    "billing": ("app/services/trial.py", "app/repositories/payment.py"),
}

REST = "everything else"


@dataclass
class Tally:
    covered: int = 0
    total: int = 0

    def add(self, summary: dict[str, int]) -> None:
        self.covered += summary["covered_lines"] + summary["covered_branches"]
        self.total += summary["num_statements"] + summary["num_branches"]

    @property
    def percent(self) -> float:
        # A group with nothing measurable is not a failure; it is a group whose
        # code has not been written yet.
        return 100.0 if self.total == 0 else 100.0 * self.covered / self.total


def _group_of(path: str) -> str:
    normalised = path.replace("\\", "/")
    for name, prefixes in CRITICAL_GROUPS.items():
        if any(normalised.startswith(prefix) for prefix in prefixes):
            return name
    return REST


def _load_report() -> dict[str, object]:
    """Render the collected data as JSON, without leaving a file behind."""
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "coverage.json"
        collected = coverage.Coverage()
        collected.load()
        collected.json_report(outfile=str(destination))
        report: dict[str, object] = json.loads(destination.read_text(encoding="utf-8"))
    return report


def _weakest(files: dict[str, dict[str, dict[str, int]]], group: str, limit: int = 5) -> list[str]:
    """The files dragging a failing group down, worst first.

    A gate that says "agent-core is at 74%" and stops there sends whoever
    reads it back to the report to find out which file to write a test for.
    """
    scored: list[tuple[float, str]] = []
    for path, entry in files.items():
        if _group_of(path) != group:
            continue
        tally = Tally()
        tally.add(entry["summary"])
        if tally.total:
            scored.append((tally.percent, path.replace("\\", "/")))
    return [f"{percent:5.1f}%  {path}" for percent, path in sorted(scored)[:limit]]


def main(argv: Iterable[str] = ()) -> int:
    del argv
    report = _load_report()
    files: dict[str, dict[str, dict[str, int]]] = report["files"]  # type: ignore[assignment]

    tallies: dict[str, Tally] = {name: Tally() for name in (*CRITICAL_GROUPS, REST)}
    for path, entry in files.items():
        tallies[_group_of(path)].add(entry["summary"])

    failures: list[str] = []
    print("\nCoverage by module group (line + branch)")
    print("-" * 62)
    for name, tally in tallies.items():
        minimum = BASELINE_MINIMUM if name == REST else CRITICAL_MINIMUM
        passed = tally.percent >= minimum
        verdict = "ok" if passed else "BELOW"
        print(
            f"{name:<24} {tally.percent:6.2f}%  (needs {minimum:.0f}%)  "
            f"{tally.covered}/{tally.total}  {verdict}"
        )
        if not passed:
            failures.append(name)
    print("-" * 62)

    if not failures:
        print("Coverage standard met.\n")
        return 0

    for name in failures:
        print(f"\nWeakest files in {name!r}:")
        for line in _weakest(files, name):
            print(f"  {line}")
    print(
        "\nThe standard is in leo-sdd/constitution.md: 80% on agent-core, tools, "
        "channels and billing; 70% on the rest.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
