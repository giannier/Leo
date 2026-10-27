"""The conversations the owner pasted from the simulator, against the real model.

`make evals` runs them with the rest. The report goes to `$REAL_CONVERSATIONS_REPORT` (by default
the temp directory). `JUDGE=1` adds a reading of each transcript by the model as a person would
read it (reported); `JUDGE=gate` also fails the run when the judge finds one unreasonable.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from evals.real_conversations import (
    ALL_SCENARIOS,
    HELD_OUT,
    NAYU,
    NAYU_FRESH,
    SCENARIOS,
    run_all,
)
from sqlalchemy.orm import Session

pytestmark = pytest.mark.evals

REPORT = Path(
    os.getenv("REAL_CONVERSATIONS_REPORT") or Path(tempfile.gettempdir()) / "real_conversations.md"
)


class TestRealConversations:
    async def test_the_assistant_holds_a_reasonable_conversation(self, db: Session, llm) -> None:  # type: ignore[no-untyped-def]
        mode = os.getenv("JUDGE", "")
        only = os.getenv("REAL_ONLY")
        pool = {"owner": SCENARIOS, "new": HELD_OUT, "nayu": NAYU, "fresh": NAYU_FRESH}.get(
            os.getenv("REAL_SET", ""), ALL_SCENARIOS
        )
        # `REAL_ONLY="nayu 1;nayu 7"`: the scenarios whose name starts with any of them.
        prefixes = [p for p in (only or "").split(";") if p]
        scenarios = [s for s in pool if not prefixes or s.name.startswith(tuple(prefixes))]
        report = await run_all(db, scenarios, llm=llm, judged=bool(mode))
        REPORT.write_text(report.render(), encoding="utf-8")  # noqa: ASYNC240 - once, at the end
        assert report.ok, "\n" + report.render()
        if mode == "gate":
            unreasonable = [
                o.scenario.name
                for o in report.outcomes
                if o.judge and o.judge.get("razonable") is False
            ]
            assert not unreasonable, f"el juez no los ve razonables: {unreasonable}"
