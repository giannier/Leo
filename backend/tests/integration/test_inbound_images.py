"""How a customer's photo reaches the proof pipeline (Feature 004, TASK-429).

Three paths, each ending in the same `ProofPipeline`: the simulator (the photo is put
in private storage when it arrives), the real channel (the webhook only carries a
reference; the turn downloads it with the tenant's credential) and the burst reader
that turns stored messages into the turn's images.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.models.conversation import Message
from app.models.whatsapp import WhatsAppNumber
from app.orders.proofs import InboundImage, ReceivedImage
from app.process.inbound import media_of, payload_of, stored_image_of
from app.process.templates.base import Stage
from app.providers.storage.mock import MockStorageProvider
from app.providers.whatsapp.base import (
    DownloadedMedia,
    InboundMedia,
    InboundMessage,
    WhatsAppAuthError,
    WhatsAppTransientError,
)
from app.services.channel import ConversationResponder
from app.services.inbound_media import InboundImageReader
from app.services.sandbox import SandboxService
from tests.integration.test_proof_pipeline import PHOTO, World, receipt, vision

CREDENTIALS = {"waba_id": "102290129340398", "phone_number_id": "1122334455", "api_key": "yc_key_1"}


class FakeDownloads:
    """A provider that serves one file, or fails."""

    def __init__(self, media: DownloadedMedia | Exception) -> None:
        self.media = media
        self.links: list[str] = []

    async def download_media(
        self, credentials: Any, *, link: str, max_bytes: int
    ) -> DownloadedMedia:
        self.links.append(link)
        assert credentials.values["api_key"] == "yc_key_1"
        if isinstance(self.media, Exception):
            raise self.media
        return self.media


def connect_number(db: Session, world: World) -> None:
    db.add(
        WhatsAppNumber(
            tenant_id=world.tenant.id,
            phone_e164=f"+5191{uuid.uuid4().int % 10**7:07d}",
            display_name="Tienda",
            provider="ycloud",
            status="connected",
            credentials_enc=encrypt_secret(json.dumps(CREDENTIALS)),
        )
    )
    db.flush()


class TestTheSimulatorKeepsThePhoto:
    async def test_a_photo_sent_at_checkout_becomes_a_proof(self, db: Session) -> None:
        world = World(db, sandbox=True)
        storage = MockStorageProvider()
        world.storage = storage
        llm = vision(receipt())
        service = SandboxService(db, world.tenant.id, llm=llm, storage=storage)

        turn = await service.send(world.conversation.id, content="[imagen]", image=PHOTO)

        row = turn.customer_message
        assert row.msg_type == "image"
        stored = stored_image_of(row.payload)
        assert stored is not None and stored[1] == "image/jpeg"
        assert stored[0].startswith(f"tenants/{world.tenant.id}/sandbox/")
        assert storage.get(stored[0]) == PHOTO.data
        [proof] = world.stored_proofs()
        assert proof.estado == "por_verificar"
        assert turn.result.stage_after is Stage.VERIFICACION
        # The receipt copy is a separate object, in the proofs folder.
        assert proof.storage_key != stored[0]

    async def test_without_storage_it_is_only_context(self, db: Session) -> None:
        world = World(db, sandbox=True)
        llm = vision(receipt())
        service = SandboxService(db, world.tenant.id, llm=llm)

        turn = await service.send(world.conversation.id, content="[imagen]", image=PHOTO)

        assert world.stored_proofs() == []
        assert turn.result.stage_after is Stage.COBRO
        assert llm.vision_calls == []


class TestTheRealChannelDownloadsIt:
    async def test_the_reference_in_the_webhook_is_stored_and_read_back(self) -> None:
        message = InboundMessage(
            provider_message_id="wamid.1",
            from_phone="+51987654321",
            to_phone="+51912345678",
            profile_name=None,
            msg_type="image",
            text=None,
            sent_at=datetime.now(UTC),
            media=InboundMedia(
                media_id="55",
                link="https://api.ycloud.com/v2/whatsapp/media/download/55",
                mime_type="image/jpeg",
                caption="mi pago",
            ),
        )

        payload = payload_of(message)

        assert payload is not None
        assert media_of(payload) == message.media

    async def test_a_burst_with_a_photo_becomes_the_turns_images(self, db: Session) -> None:
        world = World(db, sandbox=False)
        link = "https://api.ycloud.com/v2/whatsapp/media/download/55"
        photo = Message(
            tenant_id=world.tenant.id,
            conversation_id=world.conversation.id,
            direction="inbound",
            author="customer",
            content="[imagen]",
            msg_type="image",
            provider_message_id="wamid.photo",
            payload={"media": {"media_id": "55", "link": link, "mime_type": "image/jpeg"}},
        )
        db.add(photo)
        db.flush()

        turn = ConversationResponder._read_burst([photo])

        assert turn.image is True
        assert turn.images == (
            InboundImage(
                message_id=photo.id,
                media=InboundMedia(media_id="55", link=link, mime_type="image/jpeg"),
            ),
        )

    async def test_the_responder_downloads_records_and_stays_quiet_when_paused(
        self, db: Session
    ) -> None:
        world = World(db, sandbox=False)
        connect_number(db, world)
        link = "https://api.ycloud.com/v2/whatsapp/media/download/55"
        photo = Message(
            tenant_id=world.tenant.id,
            conversation_id=world.conversation.id,
            direction="inbound",
            author="customer",
            content="[imagen]",
            msg_type="image",
            provider_message_id="wamid.photo",
            payload={"media": {"media_id": "55", "link": link, "mime_type": "image/jpeg"}},
        )
        db.add(photo)
        db.flush()
        provider = FakeDownloads(DownloadedMedia(PHOTO.data, "image/jpeg"))
        llm = vision(receipt())
        responder = ConversationResponder(
            db,
            world.tenant.id,
            llm=llm,
            storage=world.storage,
            whatsapp=provider,  # type: ignore[arg-type]
            notices=world.notices,
        )

        answers = await responder.respond_turn(world.conversation.id)

        # The test tenant is not on a live plan, so the gate keeps the bot quiet;
        # the receipt is recorded and announced all the same.
        assert answers == []
        assert provider.links == [link]
        [proof] = world.stored_proofs()
        assert proof.estado == "por_verificar"
        assert world.notices.proofs == [proof.id]


class TestTheReader:
    @pytest.fixture
    def world(self, db: Session) -> World:
        return World(db, sandbox=False)

    async def test_it_reads_a_stored_photo(self, db: Session, world: World) -> None:
        storage = MockStorageProvider()
        storage.put("k.jpg", b"bytes", content_type="image/jpeg")
        reader = InboundImageReader(db, world.tenant.id, storage=storage)

        image = await reader.read(InboundImage(uuid.uuid4(), stored=("k.jpg", "image/jpeg")))

        assert image == ReceivedImage(b"bytes", "image/jpeg")

    async def test_a_missing_or_unsupported_object_is_none(self, db: Session, world: World) -> None:
        storage = MockStorageProvider()
        storage.put("k.gif", b"bytes", content_type="image/gif")
        reader = InboundImageReader(db, world.tenant.id, storage=storage)

        assert (
            await reader.read(InboundImage(uuid.uuid4(), stored=("nada.jpg", "image/jpeg"))) is None
        )
        assert await reader.read(InboundImage(uuid.uuid4(), stored=("k.gif", "image/gif"))) is None
        assert await reader.read(InboundImage(uuid.uuid4())) is None

    async def test_it_downloads_with_the_tenants_number(self, db: Session, world: World) -> None:
        connect_number(db, world)
        provider = FakeDownloads(DownloadedMedia(b"jpg", "image/jpeg"))
        reader = InboundImageReader(db, world.tenant.id, storage=None, provider=provider)  # type: ignore[arg-type]
        media = InboundMedia(media_id="1", link="https://api.ycloud.com/x", mime_type="image/jpeg")

        image = await reader.read(InboundImage(uuid.uuid4(), media=media))

        assert image == ReceivedImage(b"jpg", "image/jpeg")

    async def test_the_declared_type_stands_in_for_a_missing_one(
        self, db: Session, world: World
    ) -> None:
        connect_number(db, world)
        provider = FakeDownloads(DownloadedMedia(b"png", ""))
        reader = InboundImageReader(db, world.tenant.id, storage=None, provider=provider)  # type: ignore[arg-type]
        media = InboundMedia(link="https://api.ycloud.com/x", mime_type="image/png")

        image = await reader.read(InboundImage(uuid.uuid4(), media=media))

        assert image == ReceivedImage(b"png", "image/png")

    @pytest.mark.parametrize(
        "failure",
        [
            WhatsAppAuthError("no"),
            WhatsAppTransientError("down"),
            DownloadedMedia(b"", "image/jpeg"),
        ],
    )
    async def test_a_failed_download_is_none_not_an_exception(
        self, db: Session, world: World, failure: Any
    ) -> None:
        connect_number(db, world)
        provider = FakeDownloads(failure)
        reader = InboundImageReader(db, world.tenant.id, storage=None, provider=provider)  # type: ignore[arg-type]
        media = InboundMedia(link="https://api.ycloud.com/x", mime_type="image/jpeg")

        assert await reader.read(InboundImage(uuid.uuid4(), media=media)) is None

    async def test_without_a_connected_number_there_is_nothing_to_download_with(
        self, db: Session, world: World
    ) -> None:
        provider = FakeDownloads(DownloadedMedia(b"jpg", "image/jpeg"))
        reader = InboundImageReader(db, world.tenant.id, storage=None, provider=provider)  # type: ignore[arg-type]
        media = InboundMedia(link="https://api.ycloud.com/x", mime_type="image/jpeg")

        assert await reader.read(InboundImage(uuid.uuid4(), media=media)) is None
        assert provider.links == []
