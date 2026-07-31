"""`deploy/env.reference` must never fall behind `Settings`.

`.env.example` was written once in TASK-001 and then quietly rotted: by
TASK-009 it was missing 24 of 41 variables, which is how a Resend key can be
"configured" in a file the application never reads and the failure looks like
the email provider being broken.

A generated file plus this test makes that impossible - adding a setting
without regenerating the reference fails the build.
"""

from __future__ import annotations

from scripts.env_reference import REFERENCE_PATH, render

from app.core.config import Settings


class TestEnvReference:
    def test_the_checked_in_file_matches_the_model(self) -> None:
        """Run `make env-reference` if this fails."""
        assert REFERENCE_PATH.exists(), f"{REFERENCE_PATH} is missing"
        assert REFERENCE_PATH.read_text(encoding="utf-8") == render(), (
            "deploy/env.reference is stale. Regenerate it with `make env-reference`."
        )

    def test_every_setting_is_documented(self) -> None:
        rendered = render()
        for name in Settings.model_fields:
            assert f"{name.upper()}=" in rendered, f"{name} missing from the reference"

    def test_secrets_are_never_given_a_value(self) -> None:
        """A committed placeholder credential is worse than an empty one: it
        looks configured, and `SECRET_KEY` in particular would sign tokens
        anyone reading the repository could forge."""
        for line in render().splitlines():
            if line.startswith(("RESEND_API_KEY", "NVIDIA_API_KEY", "OPENAI_API_KEY")):
                assert line.endswith("="), f"reference ships a value: {line}"
            if line.startswith(("SECRET_KEY", "CREDENTIALS_ENCRYPTION_KEY")):
                assert line.endswith("="), f"reference ships a value: {line}"
