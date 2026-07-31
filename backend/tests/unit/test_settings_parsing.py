"""Settings that a person has to type by hand must accept what they type.

`CORS_ORIGINS` is the cautionary tale. It is a `list[str]`, and
pydantic-settings parses those from the environment as JSON - so the value
`deploy/env.reference` documented, `http://localhost:3000`, raised a
`SettingsError` during import. The application refused to start, and the
error named a parser rather than the config line that caused it.

Nothing caught it because no test and no CI job ever set the variable: the
default was always used.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


class TestCorsOrigins:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # The form a person writes, and the one our own reference file
            # documents.
            ("http://localhost:3000", ["http://localhost:3000"]),
            # Several, with the spaces somebody naturally leaves in.
            (
                "https://panel.leo.pe, https://admin.leo.pe",
                ["https://panel.leo.pe", "https://admin.leo.pe"],
            ),
            ("https://a.pe,https://b.pe", ["https://a.pe", "https://b.pe"]),
            # JSON still works: a deployment already written that way must not
            # break on upgrade.
            ('["https://panel.leo.pe"]', ["https://panel.leo.pe"]),
            # Empty means "allow nothing", not "crash".
            ("", []),
            ("   ", []),
            # A trailing comma is a typo, not a reason to refuse to boot.
            ("https://a.pe,", ["https://a.pe"]),
        ],
    )
    def test_it_accepts_what_people_actually_write(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
    ) -> None:
        monkeypatch.setenv("CORS_ORIGINS", raw)
        assert Settings().cors_origins == expected

    def test_the_default_stands_when_the_variable_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        assert Settings().cors_origins == ["http://localhost:3000"]

    def test_the_documented_value_boots_the_application(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reads `deploy/env.reference` and uses whatever it recommends.

        The bug was a mismatch between the file we tell people to copy and the
        parser that reads it. Asserting against a literal here would let the
        two drift apart again.
        """
        from pathlib import Path

        reference = Path(__file__).resolve().parents[3] / "deploy" / "env.reference"
        documented = next(
            line.split("=", 1)[1].strip()
            for line in reference.read_text(encoding="utf-8").splitlines()
            if line.startswith("CORS_ORIGINS=")
        )

        monkeypatch.setenv("CORS_ORIGINS", documented)
        assert Settings().cors_origins == ["http://localhost:3000"]
