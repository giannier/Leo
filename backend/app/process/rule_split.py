"""Cut a stage's instruction text into rules, and put them back (Feature 017, TASK-460).

The stage texts of `process/templates/*` were written as prose for one prompt.
The layered configuration (017, C-01) needs them as **rules**: one instruction
each, with a stable `rule_key`, that a business can switch off. This module is
the one place that knows how the texts are shaped:

* a bullet (`- ...`) is one rule, with its indented continuation lines;
* a header that ends in a colon (`Si el pago es previo:`) keeps the bullets
  under it: switching off half a branch would leave the other half orphaned;
* a paragraph wrapped over several lines is one rule up to its full stop;
* a quoted example line (`"¡Buenísimo! ..."`) belongs to the instruction above;
* a blank line stays at the end of the rule before it.

`join_rules(split_rules(text)) == text` holds for every shipped text, which is
what lets the prompt read the rules back from the database and stay identical.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

_ENDS_SENTENCE = (".", ":", "?", "!", '"', ")")
_SLUG_WORDS = 5
_STOP_WORDS = frozenset(
    {"a", "al", "de", "del", "el", "en", "la", "las", "lo", "los", "si", "un", "una", "y", "o"}
)


def _is_bullet(line: str) -> bool:
    return line.startswith("- ")


def _is_header(block: Sequence[str]) -> bool:
    first = block[0]
    return not _is_bullet(first) and first.rstrip().endswith(":")


def split_rules(text: str) -> list[str]:
    """The rules of one stage text, in order. Joining them with newlines gives it back."""
    blocks: list[list[str]] = []
    for line in text.split("\n"):
        if not blocks:
            blocks.append([line])
            continue
        current = blocks[-1]
        if not line or line.startswith(" ") or (line.startswith('"') and current[-1]):
            current.append(line)
        elif _is_bullet(line):
            if _is_header(current):
                current.append(line)
            else:
                blocks.append([line])
        elif (
            _is_header(current)
            or current[-1].rstrip().endswith(_ENDS_SENTENCE)
            or not current[-1]
        ):
            blocks.append([line])
        else:
            # A paragraph wrapped over several lines.
            current.append(line)
    return ["\n".join(block) for block in blocks]


def join_rules(rules: Iterable[str]) -> str:
    """The stage text from its rules (the inverse of `split_rules`)."""
    return "\n".join(rules)


def _slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = [w for w in re.findall(r"[a-z0-9]+", plain.lower()) if w not in _STOP_WORDS]
    return "_".join(words[:_SLUG_WORDS]) or "regla"


def rule_keys(stage: str, rules: Sequence[str]) -> list[str]:
    """A readable, unique key per rule: `COBRO.pago_previo`, ...

    Generated once, when a version is first written; after that a key is data and
    never recomputed, so rewording a rule in a new version keeps its key.
    """
    keys: list[str] = []
    for rule in rules:
        base = f"{stage}.{_slug(rule.lstrip('- '))}"
        key, n = base, 2
        while key in keys:
            key, n = f"{base}_{n}", n + 1
        keys.append(key)
    return keys
