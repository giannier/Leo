"""Platform users and their short-lived credentials.

Users are global, not tenant-scoped: one person can belong to several tenants
through `tenant_users`. That is why nothing in this module carries a
`tenant_id`, and why these tables are outside the tenant-filter rule.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_check,
)

OTP_PURPOSES = ("phone_verify",)
EMAIL_TOKEN_PURPOSES = ("verify_email", "reset_password")


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A person who can sign in to the panel."""

    __tablename__ = "users"

    # CITEXT so uniqueness is case-insensitive in the database itself.
    # Lowercasing in application code would not survive a concurrent insert;
    # the unique index has to be the one enforcing it.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)

    phone_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class OtpCode(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One-time code sent over WhatsApp or SMS to verify a contact phone."""

    __tablename__ = "otp_codes"
    __table_args__ = (enum_check("purpose", OTP_PURPOSES, "otp_codes_purpose"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    # Only the hash is stored, so a leaked database yields no usable codes.
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Capped at 5 by the verification service.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class EmailToken(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Single-use token for email verification and password reset."""

    __tablename__ = "email_tokens"
    __table_args__ = (
        enum_check("purpose", EMAIL_TOKEN_PURPOSES, "email_tokens_purpose"),
        # Issuing a new token retires the user's live ones, which is a
        # lookup by (user_id, purpose) over the unconsumed rows only. The
        # table is append-only and grows with every registration and every
        # resend, so without this the retire step degrades into a sequential
        # scan. Partial, because consumed rows are never the target.
        Index(
            "ix_email_tokens_user_id_purpose_active",
            "user_id",
            "purpose",
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshToken(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One link in a rotating session chain.

    Every use of a refresh token revokes it and issues a replacement, so a
    single sign-in leaves behind a chain of rows: one live, the rest revoked.
    `family_id` is what ties that chain together - it is minted at login and
    copied to every successor.

    The chain exists to make theft detectable. A stolen token is
    indistinguishable from the real one, so the give-away is not the token
    itself but the *duplicate use*: whichever party rotates second presents a
    row that is already revoked. There is no way to tell victim from thief at
    that point, so the whole family is revoked and both are forced back
    through the password. Without `family_id` the choice would be between
    revoking nothing (theft goes unnoticed indefinitely) and revoking every
    session the user has anywhere, including the ones on devices that were
    never involved.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    # Indexed because revoking a family is a write over every row that shares
    # this value, on the hot path of a suspected token theft.
    family_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
