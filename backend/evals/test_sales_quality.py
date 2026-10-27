"""The agent's sales conversation quality, against the real model.

`make evals` runs this with the other evals. The report (with every transcript) is written
to `$SALES_QUALITY_REPORT`, by default in the temp directory, so it can go in the PR and be
read. `SALES_QUALITY_TEMPLATES=productos,servicios` picks the templates.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from evals.sales_quality import (
    FRESH2_SCENARIOS,
    FRESH3_SCENARIOS,
    FRESH_SCENARIOS,
    SCENARIOS,
    run_quality,
)
from sqlalchemy.orm import Session

pytestmark = pytest.mark.evals

REPORT = Path(os.getenv("SALES_QUALITY_REPORT") or Path(tempfile.gettempdir()) / "sales_quality.md")


class TestSalesQuality:
    async def test_the_agent_sounds_like_a_seller(self, db: Session, llm) -> None:  # type: ignore[no-untyped-def]
        templates = (os.getenv("SALES_QUALITY_TEMPLATES") or "productos,servicios").split(",")
        # `SALES_QUALITY_SET=fresh`: the scenarios written for a validation and never tuned against.
        scenarios = {
            "fresh": FRESH_SCENARIOS,
            "fresh2": FRESH2_SCENARIOS,
            "fresh3": FRESH3_SCENARIOS,
        }.get(os.getenv("SALES_QUALITY_SET", ""), SCENARIOS)
        report = await run_quality(db, scenarios, templates, llm=llm)
        REPORT.write_text(report.render(), encoding="utf-8")  # noqa: ASYNC240 - once, at the end
        assert report.ok, "\n" + report.render()
