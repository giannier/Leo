"""Recorded provider payloads, anonymised.

Kept as the JSON a vendor actually sends rather than as Python literals. The
whole risk this guards against - `plan.md`'s "payloads de YCloud distintos a
la doc" - is that our idea of the shape drifts from the real one, and a file
that can be replaced by pasting a real capture over it keeps that honest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent


def load(name: str) -> dict[str, Any]:
    """One recorded payload, by path relative to this package."""
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def load_raw(name: str) -> bytes:
    """The same payload as bytes, exactly as it arrived.

    Signature verification is over the raw body, so anything that tests it
    has to work from the unparsed bytes - re-serialising the parsed object
    changes key order and whitespace and would verify a different string.
    """
    return (FIXTURES / name).read_bytes()
