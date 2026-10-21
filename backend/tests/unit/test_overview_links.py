"""Every link of the assistant's activation checklist must open a real page."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.overview import STEP_ACTIONS

PANEL = Path(__file__).resolve().parents[3] / "frontend" / "app" / "(panel)"


@pytest.mark.skipif(not PANEL.is_dir(), reason="the frontend is not in this checkout")
@pytest.mark.parametrize(("step", "route"), sorted(STEP_ACTIONS.items()))
def test_a_step_links_to_a_page_that_exists(step: str, route: str) -> None:
    """The checklist once sent people to /asistente/simulador, /whatsapp and
    /productos, none of which exist: they landed on a 404."""
    # A step may open a section or a wizard step (`?bloque=contacto`): the page is the path.
    page = PANEL / route.split("?")[0].strip("/") / "page.tsx"
    assert page.is_file(), f"{step} links to {route}, but {page} does not exist"


APP = Path(__file__).resolve().parents[2] / "app"
LINK = re.compile(r"""(?:frontend_base_url[^"']*\}|_panel_url\(settings,\s*")(/[a-z\-]+)""")


@pytest.mark.skipif(not PANEL.is_dir(), reason="the frontend is not in this checkout")
def test_every_panel_link_in_an_email_opens_a_real_page() -> None:
    """Emails once linked to /canal, /plan and /planes, which do not exist."""
    found = {
        (path.name, route) for path in APP.rglob("*.py") for route in LINK.findall(path.read_text())
    }
    assert found, "the pattern no longer finds any panel link; update it"
    missing = sorted(
        f"{name}: {route}"
        for name, route in found
        if not (PANEL / route.strip("/") / "page.tsx").is_file()
    )
    assert not missing, missing
