"""What an inbound message remembers of its webhook event (Feature 004, TASK-421).

The tap and the ad are written to `messages.payload` when the webhook is applied and
read back when the turn runs, after the debounce window. What comes back is read
defensively: the payload is provider-shaped data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.process.inbound import (
    interactive_of,
    location_line,
    location_of,
    payload_of,
    referral_of,
)
from app.providers.whatsapp.base import (
    InboundLocation,
    InboundMessage,
    InteractiveReply,
    Referral,
)


def _inbound(**extra: Any) -> InboundMessage:
    return InboundMessage(
        provider_message_id="wamid.1",
        from_phone="+51987654321",
        to_phone="+51912345678",
        profile_name=None,
        msg_type="text",
        text="hola",
        sent_at=datetime.now(UTC),
        **extra,
    )


class TestWhatIsStored:
    def test_a_plain_message_stores_nothing(self) -> None:
        assert payload_of(_inbound()) is None

    def test_a_tap_round_trips(self) -> None:
        tap = InteractiveReply(id="camino:certificacion", title="Solo certificar")

        stored = payload_of(_inbound(interactive=tap))

        assert interactive_of(stored) == tap
        assert referral_of(stored) is None

    def test_an_ad_round_trips_without_its_empty_fields(self) -> None:
        ad = Referral(source_id="ad-1", headline="Certifícate", source_url="https://fb.me/x")

        stored = payload_of(_inbound(referral=ad))

        assert stored is not None
        assert stored["referral"] == {
            "source_id": "ad-1",
            "headline": "Certifícate",
            "source_url": "https://fb.me/x",
        }
        assert referral_of(stored) == ad

    def test_both_travel_together(self) -> None:
        tap = InteractiveReply(id="producto:1", title="Excel", description="Nivel avanzado")
        ad = Referral(source_id="ad-2")

        stored = payload_of(_inbound(interactive=tap, referral=ad))

        assert interactive_of(stored) == tap
        assert referral_of(stored) == ad


class TestReadingIsDefensive:
    @pytest.mark.parametrize("payload", [None, {}, {"interactive": None}, {"interactive": "x"}])
    def test_no_tap(self, payload: dict[str, Any] | None) -> None:
        assert interactive_of(payload) is None

    def test_a_tap_without_an_id_is_not_a_tap(self) -> None:
        assert interactive_of({"interactive": {"title": "x"}}) is None
        assert interactive_of({"interactive": {"id": "  ", "title": "x"}}) is None

    def test_a_tap_without_a_title_keeps_its_id(self) -> None:
        tap = interactive_of({"interactive": {"id": "accion:ver"}})

        assert tap == InteractiveReply(id="accion:ver", title="", description=None)

    @pytest.mark.parametrize("payload", [None, {}, {"referral": None}, {"referral": 3}])
    def test_no_ad(self, payload: dict[str, Any] | None) -> None:
        assert referral_of(payload) is None

    def test_non_text_values_are_dropped(self) -> None:
        ad = referral_of({"referral": {"source_id": 5, "headline": "ok", "body": ""}})

        assert ad == Referral(headline="ok")


class TestAPinThePayloadKeeps:
    def test_a_pin_round_trips(self) -> None:
        pin = InboundLocation(lat=-12.0464, lng=-77.0428, nombre="Plaza Mayor")

        stored = payload_of(_inbound(location=pin))

        assert stored == {"location": {"lat": -12.0464, "lng": -77.0428, "nombre": "Plaza Mayor"}}
        assert location_of(stored) == pin

    @pytest.mark.parametrize(
        "stored",
        [
            None,
            {},
            {"location": "somewhere"},
            {"location": {"lat": "north", "lng": 1}},
            {"location": {"lat": 95, "lng": 1}},
            {"location": {"lat": 1}},
        ],
    )
    def test_what_is_not_a_pin_on_the_globe_reads_back_as_none(self, stored: Any) -> None:
        assert location_of(stored) is None

    def test_the_conversation_reads_it_as_words_never_as_coordinates(self) -> None:
        bare = location_line(InboundLocation(lat=-12.0, lng=-77.0))
        named = location_line(
            InboundLocation(lat=-12.0, lng=-77.0, nombre="Casa", direccion="Av. Perú 123")
        )

        assert bare == "[Ubicación compartida por WhatsApp]"
        assert named == "[Ubicación compartida por WhatsApp] Casa · Av. Perú 123"
        assert "12.0" not in bare + named
