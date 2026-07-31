"""Tests for one-time code generation.

A six-digit code is roughly twenty bits, which is guessable on purpose - it
has to be readable over the phone and retypeable. What makes that acceptable
is not the code itself but the two bounds around it in `OtpService`: five
attempts, counted under a row lock, and a ten-minute expiry. Neither is
decoration, and removing either turns this into a weak credential.
"""

from __future__ import annotations

import pytest

from app.core.security import OTP_DIGITS, generate_otp_code


class TestGenerateOtpCode:
    def test_default_length_matches_otp_digits(self) -> None:
        """Callers relying on the constant must get a code of that size."""
        assert len(generate_otp_code()) == OTP_DIGITS

    def test_result_is_all_decimal_digits(self) -> None:
        """The API validates the submitted code against `^\\d{6}$`.

        A non-digit here would produce codes the endpoint rejects as malformed
        before they are ever compared.
        """
        assert generate_otp_code().isdecimal()

    @pytest.mark.parametrize("digits", [4, 6, 8])
    def test_custom_digit_count_is_honoured(self, digits: int) -> None:
        code = generate_otp_code(digits=digits)
        assert len(code) == digits
        assert code.isdecimal()

    def test_leading_zeros_are_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Dropping a leading zero yields a code that no longer matches.

        The customer is told six digits and types six digits; a five-character
        value fails the endpoint's pattern check and the verification looks
        broken for reasons nobody can reproduce, because it only happens for
        one code in ten.
        """
        monkeypatch.setattr("app.core.security.secrets.randbelow", lambda _: 42)
        assert generate_otp_code(digits=6) == "000042"

    def test_consecutive_codes_are_not_constant(self) -> None:
        """Smoke check that the generator is not returning a fixed value."""
        codes = {generate_otp_code() for _ in range(200)}
        assert len(codes) > 1
