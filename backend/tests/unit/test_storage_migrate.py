"""Moving files between backends, and the guarantees that make it safe to try.

A migration tool that loses one file silently is worse than none, so these tests are
about the ways it could: copying something that does not match, overwriting
something that was already there, deleting the original of a copy that never
verified, or stopping at the first problem and leaving the rest unknown.
"""

from __future__ import annotations

import pytest
from scripts.storage_migrate import migrate

from app.providers.storage import MockStorageProvider, ObjectNotFoundError, StorageError

KEY_A = "tenants/6f1c2d3e-0000-4000-8000-000000000001/media/a.png"
KEY_B = "tenants/6f1c2d3e-0000-4000-8000-000000000001/proofs/b.png"
KEY_C = "tenants/6f1c2d3e-0000-4000-8000-000000000001/knowledge/c.pdf"


def _seeded() -> MockStorageProvider:
    source = MockStorageProvider()
    source.put(KEY_A, b"photo-bytes", content_type="image/png")
    source.put(KEY_B, b"receipt-bytes", content_type="image/jpeg")
    source.put(KEY_C, b"%PDF-manual", content_type="application/pdf")
    return source


def _listing(source: MockStorageProvider) -> list[tuple[str, str]]:
    return [(key, content_type) for key, (_, content_type) in source.objects.items()]


class Corrupting(MockStorageProvider):
    """A destination that stores something other than what it was given."""

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        return super().put(key, b"damaged:" + data, content_type=content_type)


class Counting(MockStorageProvider):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        self.writes = 0

    def get(self, key: str) -> bytes:
        self.reads += 1
        return super().get(key)

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self.writes += 1
        return super().put(key, data, content_type=content_type)


class TestCopying:
    def test_every_file_arrives_with_its_content_and_type(self) -> None:
        source, destination = _seeded(), MockStorageProvider()

        report = migrate(_listing(source), source, destination, apply=True, log=lambda _: None)

        assert report.copied == 3 and not report.failed
        assert destination.objects == source.objects

    def test_the_bytes_copied_are_counted(self) -> None:
        source, destination = _seeded(), MockStorageProvider()

        report = migrate(_listing(source), source, destination, apply=True, log=lambda _: None)

        assert report.bytes_copied == len(b"photo-bytes") + len(b"receipt-bytes") + len(
            b"%PDF-manual"
        )

    def test_a_second_run_copies_nothing(self) -> None:
        source, destination = _seeded(), MockStorageProvider()
        migrate(_listing(source), source, destination, apply=True, log=lambda _: None)

        report = migrate(_listing(source), source, destination, apply=True, log=lambda _: None)

        assert (report.copied, report.already_there, report.failed) == (0, 3, [])

    def test_the_source_is_left_alone_by_default(self) -> None:
        source, destination = _seeded(), MockStorageProvider()

        migrate(_listing(source), source, destination, apply=True, log=lambda _: None)

        assert len(source.objects) == 3


class TestADryRun:
    def test_it_reads_nothing_and_writes_nothing(self) -> None:
        source, destination = _seeded(), Counting()
        counting_source = Counting()
        counting_source.objects = dict(source.objects)

        report = migrate(
            _listing(source), counting_source, destination, apply=False, log=lambda _: None
        )

        assert (counting_source.reads, destination.writes) == (0, 0)
        assert destination.objects == {}
        assert report.copied == 3

    def test_it_says_what_is_already_there(self) -> None:
        source, destination = _seeded(), MockStorageProvider()
        destination.put(KEY_A, b"photo-bytes", content_type="image/png")

        report = migrate(_listing(source), source, destination, apply=False, log=lambda _: None)

        assert (report.copied, report.already_there) == (2, 1)


class TestNeverOverwritingAndNeverTrustingACopy:
    def test_a_file_already_there_with_different_content_is_reported_not_replaced(self) -> None:
        source, destination = _seeded(), MockStorageProvider()
        destination.put(KEY_A, b"somebody else's bytes", content_type="image/png")

        report = migrate(_listing(source), source, destination, apply=True, log=lambda _: None)

        assert destination.objects[KEY_A][0] == b"somebody else's bytes"
        assert [key for key, _ in report.failed] == [KEY_A]
        assert report.copied == 2

    def test_a_copy_that_does_not_match_is_a_failure(self) -> None:
        source, destination = _seeded(), Corrupting()

        report = migrate(_listing(source), source, destination, apply=True, log=lambda _: None)

        assert report.copied == 0
        assert len(report.failed) == 3
        assert all("does not match" in reason for _, reason in report.failed)

    def test_a_failed_copy_never_costs_the_original(self) -> None:
        source, destination = _seeded(), Corrupting()

        migrate(
            _listing(source),
            source,
            destination,
            apply=True,
            delete_source=True,
            log=lambda _: None,
        )

        assert len(source.objects) == 3


class TestDeletingTheSource:
    def test_a_verified_copy_lets_the_original_go(self) -> None:
        source, destination = _seeded(), MockStorageProvider()
        original = dict(source.objects)

        report = migrate(
            _listing(source),
            source,
            destination,
            apply=True,
            delete_source=True,
            log=lambda _: None,
        )

        assert source.objects == {}
        assert destination.objects == original
        assert report.deleted_from_source == 3

    def test_only_the_files_that_verified_are_deleted(self) -> None:
        source, destination = _seeded(), MockStorageProvider()
        destination.put(KEY_B, b"conflicting", content_type="image/jpeg")

        migrate(
            _listing(source),
            source,
            destination,
            apply=True,
            delete_source=True,
            log=lambda _: None,
        )

        assert set(source.objects) == {KEY_B}

    def test_a_dry_run_never_deletes(self) -> None:
        source, destination = _seeded(), MockStorageProvider()

        migrate(
            _listing(source),
            source,
            destination,
            apply=False,
            delete_source=True,
            log=lambda _: None,
        )

        assert len(source.objects) == 3

    def test_a_file_already_in_the_destination_and_identical_may_be_deleted_from_the_source(
        self,
    ) -> None:
        """An interrupted earlier run copied it; this run finishes the job."""
        source, destination = _seeded(), MockStorageProvider()
        destination.put(KEY_A, b"photo-bytes", content_type="image/png")

        migrate(
            _listing(source),
            source,
            destination,
            apply=True,
            delete_source=True,
            log=lambda _: None,
        )

        assert source.objects == {}


class TestNotStoppingAtTheFirstProblem:
    def test_a_file_that_vanished_from_the_source_does_not_stop_the_others(self) -> None:
        source, destination = _seeded(), MockStorageProvider()
        listing = _listing(source)
        source.delete(KEY_B)  # listed, then gone

        report = migrate(listing, source, destination, apply=True, log=lambda _: None)

        assert report.copied == 2
        assert [key for key, _ in report.failed] == [KEY_B]

    def test_every_failure_is_reported_with_its_reason(self) -> None:
        class Broken(MockStorageProvider):
            def get(self, key: str) -> bytes:
                if key == KEY_C:
                    raise StorageError("the disk is on fire")
                return super().get(key)

        source = Broken()
        for key, (data, content_type) in _seeded().objects.items():
            source.put(key, data, content_type=content_type)
        messages: list[str] = []

        report = migrate(
            _listing(source), source, MockStorageProvider(), apply=True, log=messages.append
        )

        assert report.failed == [(KEY_C, "the disk is on fire")]
        assert any("the disk is on fire" in message for message in messages)

    def test_a_missing_key_is_a_not_found_for_the_mock(self) -> None:
        with pytest.raises(ObjectNotFoundError):
            MockStorageProvider().get(KEY_A)
