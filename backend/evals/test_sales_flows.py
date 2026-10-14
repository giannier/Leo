"""The sales flows of the three templates, against the real model (TASK-447).

`make evals` runs them with the golden conversations. The report is written to
`$SALES_FLOWS_REPORT` (by default the temp directory) so it can go in the PR.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from evals.sales_flows import FLOW_CASES, run_flows
from sqlalchemy.orm import Session

pytestmark = pytest.mark.evals

REPORT = Path(os.getenv("SALES_FLOWS_REPORT") or Path(tempfile.gettempdir()) / "sales_flows.md")


class TestSalesFlows:
    async def test_each_template_sells_as_its_template_says(self, db: Session, llm) -> None:  # type: ignore[no-untyped-def]
        only = os.getenv("SALES_FLOWS_ONLY")
        cases = [c for c in FLOW_CASES if not only or c.template == only]
        report = await run_flows(db, cases, llm=llm)
        REPORT.write_text(report.render(), encoding="utf-8")  # noqa: ASYNC240 - once, at the end
        assert report.ok, "\n" + report.render()
