"""The verification email template.

The body is the one piece of Spanish copy in the backend, and it interpolates
two attacker-influenced values, so both facts are pinned here.
"""

from __future__ import annotations

from app.services.emails import VERIFICATION_SUBJECT, build_verification_email


def _build(full_name: str = "Ana Quispe", url: str = "https://panel.leo.pe/verify-email?token=abc"):
    return build_verification_email(
        to="ana@example.com",
        full_name=full_name,
        verification_url=url,
        expires_in_hours=24,
    )


class TestVerificationEmail:
    def test_addressed_to_the_recipient_with_the_documented_subject(self) -> None:
        message = _build()
        assert message.to == ("ana@example.com",)
        assert message.subject == VERIFICATION_SUBJECT

    def test_link_appears_in_both_bodies(self) -> None:
        """HTML-only would be unreadable in a client that blocks HTML."""
        message = _build()
        assert "https://panel.leo.pe/verify-email?token=abc" in message.text or ""
        assert "https://panel.leo.pe/verify-email?token=abc" in message.html

    def test_states_how_long_the_link_lasts(self) -> None:
        message = _build()
        assert "24 horas" in message.html
        assert message.text is not None and "24 horas" in message.text

    def test_singular_hour_is_not_written_as_1_horas(self) -> None:
        message = build_verification_email(
            to="ana@example.com",
            full_name="Ana",
            verification_url="https://example.test/v?token=x",
            expires_in_hours=1,
        )
        assert "1 hora" in message.html
        assert "1 horas" not in message.html

    def test_name_is_escaped_before_reaching_the_html_body(self) -> None:
        """The name comes from the registration form, unvalidated beyond length."""
        message = _build(full_name='<script>alert("x")</script>')
        assert "<script>" not in message.html
        assert "&lt;script&gt;" in message.html

    def test_copy_is_spanish(self) -> None:
        message = _build()
        assert "Confirmar mi correo" in message.html
        assert "prueba" in message.html
