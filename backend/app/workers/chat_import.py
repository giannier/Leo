"""Processing a chat a business handed over (017, phase 2).

The API leaves the original in a temporary store for a few minutes and enqueues this job; the job
reads it, runs the analysis and **deletes the original in a ``finally``**, whether it worked or
not. What is kept is the result (suggestions and counts); the error, if any, is worded without
anything of the chat.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.learning.importer import ImportFailed, run_import
from app.repositories.learning import ChatImportRepository

logger = logging.getLogger(__name__)

IMPORT_JOB = "process_chat_import"
# How long the original may wait for the job: if the worker never comes, it is gone by itself.
ORIGINAL_TTL_SECONDS = 2 * 3600


def original_key(import_id: uuid.UUID) -> str:
    return f"chat-import:{import_id}"


def sellers_key(import_id: uuid.UUID) -> str:
    return f"chat-import:{import_id}:sellers"


async def process_chat_import(ctx: dict[str, Any], import_id: str, tenant_id: str) -> str:
    """ARQ entry point. Returns the final state."""
    redis = ctx["redis"]
    chat_id, tenant = uuid.UUID(import_id), uuid.UUID(tenant_id)
    state = "fallido"
    try:
        raw = await redis.get(original_key(chat_id))
        names = await redis.get(sellers_key(chat_id))
        with ctx["session_factory"]() as session:
            record = ChatImportRepository(session, tenant).get(chat_id)
            if record is None:
                return "fallido"
            try:
                if raw is None or names is None:
                    raise ImportFailed("expired", "El archivo ya no estaba: súbelo de nuevo.")
                outcome = await run_import(
                    session,
                    record,
                    bytes(raw),
                    json.loads(names),
                    llm=ctx["llm"],
                    transcriber=ctx["transcriber"],
                    actor=record.autorizado_por,
                )
            except ImportFailed as exc:
                session.rollback()
                record = ChatImportRepository(session, tenant).get(chat_id)
                if record is not None:
                    record.estado = "fallido"
                    record.error = str(exc)[:300]
                    record.terminada_en = datetime.now(UTC)
            except Exception:  # noqa: BLE001 - nothing of the chat goes to the log or the record
                logger.error("a chat import failed", extra={"tenant_id": tenant_id})
                session.rollback()
                record = ChatImportRepository(session, tenant).get(chat_id)
                if record is not None:
                    record.estado = "fallido"
                    record.error = "Ocurrió un error al procesar el chat. Inténtalo de nuevo."
                    record.terminada_en = datetime.now(UTC)
            else:
                record.estado = "listo"
                record.mensajes = outcome.mensajes
                record.conversaciones = outcome.conversaciones
                record.audios = outcome.audios
                record.audios_transcritos = outcome.audios_transcritos
                record.minutos_audio = outcome.minutos_audio
                record.sugerencias = outcome.sugerencias
                record.terminada_en = datetime.now(UTC)
                state = "listo"
            session.commit()
    finally:
        # The original is never kept: also when this job failed or the record was not found.
        await redis.delete(original_key(chat_id), sellers_key(chat_id))
    return state
