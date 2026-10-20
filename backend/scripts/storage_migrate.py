"""Copy every stored file from one backend to another: the way out of Postgres, or back.

    make storage-migrate ARGS="--from postgres --to s3"            # looks, changes nothing
    make storage-migrate ARGS="--from postgres --to s3 --apply"    # copies and verifies
    make storage-migrate ARGS="--from postgres --to s3 --apply --delete-source"

Files live in Postgres because that is the simplest thing that works today. The
day that stops being true - the database is mostly photos, or backups take too
long - the answer is an S3-compatible store, and this is what makes that a
maintenance window and not a project. It works both ways: the same command moves
files from S3 into Postgres.

Safe to stop and run again. It never overwrites: a file already in the
destination is compared by checksum and left alone if it matches, and reported if
it does not. Every copy is read back from the destination and checked against the
source before it counts. `--delete-source` removes a file from the source only
after that check, one file at a time, so an interrupted run leaves every file in
at least one place.

Switching the application over is a separate, deliberate step: set
`STORAGE_BACKEND` (and the `S3_*` settings) and restart. Run this first, with the
application stopped or quiet, so nothing is uploaded to the old store in between.
"""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import get_session_factory
from app.models.storage import StoredObject
from app.providers.storage.base import StorageError, StorageProvider
from app.providers.storage.factory import build_storage_provider
from app.providers.storage.s3_provider import S3StorageProvider

BACKENDS = ("postgres", "s3")


@dataclass
class Report:
    copied: int = 0
    already_there: int = 0
    deleted_from_source: int = 0
    bytes_copied: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def migrate(
    objects: Iterable[tuple[str, str]],
    source: StorageProvider,
    destination: StorageProvider,
    *,
    apply: bool,
    delete_source: bool = False,
    log: Callable[[str], None] = print,
) -> Report:
    """Copy `(key, content_type)` pairs from `source` to `destination`.

    Without `apply` nothing is read and nothing is written: it only says what a real
    run would do. Failures are collected and the run carries on, so one bad file does
    not hide the state of the other ten thousand.
    """
    report = Report()
    for key, content_type in objects:
        try:
            if not apply:
                if destination.exists(key):
                    report.already_there += 1
                else:
                    report.copied += 1
                continue

            data = source.get(key)
            digest = _sha(data)
            if destination.exists(key):
                if _sha(destination.get(key)) != digest:
                    report.failed.append((key, "the destination already has different content"))
                    continue
                report.already_there += 1
            else:
                destination.put(key, data, content_type=content_type)
                if _sha(destination.get(key)) != digest:
                    report.failed.append((key, "the copy does not match the original"))
                    continue
                report.copied += 1
                report.bytes_copied += len(data)

            if delete_source:
                source.delete(key)
                report.deleted_from_source += 1
        except StorageError as exc:
            report.failed.append((key, str(exc)))
            log(f"  falló {key}: {exc}")
    return report


def postgres_objects() -> Iterable[tuple[str, str]]:
    with get_session_factory()() as session:
        rows = session.execute(
            select(StoredObject.key, StoredObject.content_type).order_by(StoredObject.created_at)
        ).all()
    return [(row.key, row.content_type) for row in rows]


def s3_objects(provider: S3StorageProvider) -> Iterable[tuple[str, str]]:
    return list(provider.iter_objects())


def _provider(settings: Settings, backend: str) -> StorageProvider:
    return build_storage_provider(settings.model_copy(update={"storage_backend": backend}))


def main(*, source_backend: str, destination_backend: str, apply: bool, delete_source: bool) -> int:
    if source_backend == destination_backend:
        print("El origen y el destino son el mismo almacenamiento. Nada que hacer.")
        return 1
    if delete_source and not apply:
        print("--delete-source solo tiene sentido con --apply.")
        return 1

    settings = get_settings()
    source = _provider(settings, source_backend)
    destination = _provider(settings, destination_backend)
    objects = (
        postgres_objects() if source_backend == "postgres" else s3_objects(source)  # type: ignore[arg-type]
    )
    objects = list(objects)

    mode = "COPIANDO" if apply else "SIMULACRO (no cambia nada)"
    print(f"{mode}: {len(objects)} archivos de {source_backend} a {destination_backend}")
    # Failures are listed once, in the summary below, not as they happen as well.
    report = migrate(
        objects, source, destination, apply=apply, delete_source=delete_source, log=lambda _: None
    )

    verb = "copiados" if apply else "por copiar"
    print(f"  {report.copied} {verb}, {report.already_there} ya estaban en el destino")
    if apply:
        print(f"  {report.bytes_copied / 1_048_576:.1f} MB copiados y verificados")
        if delete_source:
            print(f"  {report.deleted_from_source} borrados del origen")
    if report.failed:
        print(f"  {len(report.failed)} con error (el resto quedó bien):")
        for key, reason in report.failed:
            print(f"    {key}: {reason}")
        return 2
    if apply:
        print("Listo. Cambia STORAGE_BACKEND y reinicia para usar el nuevo almacenamiento.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from", dest="source", choices=BACKENDS, required=True)
    parser.add_argument("--to", dest="destination", choices=BACKENDS, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="copiar de verdad (por defecto, solo simula)"
    )
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="borrar cada archivo del origen, solo después de verificar su copia",
    )
    args = parser.parse_args()
    raise SystemExit(
        main(
            source_backend=args.source,
            destination_backend=args.destination,
            apply=args.apply,
            delete_source=args.delete_source,
        )
    )
