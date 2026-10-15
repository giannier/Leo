"""Run the catalogue evals on every published template and rubro, with the platform model.

    LLM_PROFILE=openai OPENAI_API_KEY=... uv run python -m scripts.run_catalog_evals

For TASK-469 and for a check after changing the eval cases. Nothing is stored:
the run happens in a transaction that is rolled back. Exits 1 if any fails.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.models.catalog import CatalogVersion
from app.providers.llm.factory import build_provider, platform_profile
from app.services.catalog_evals import run_evals


async def main() -> int:
    llm = build_provider(platform_profile(get_settings()))
    failed = 0
    with get_session_factory()() as session:
        versions = session.execute(
            select(CatalogVersion)
            .where(CatalogVersion.estado == "publicada")
            .order_by(CatalogVersion.kind, CatalogVersion.key)
        ).scalars()
        for version in list(versions):
            run = await run_evals(session, version, llm)
            mark = "OK " if run.ok else "MAL"
            print(f"{mark} {version.kind:8} {version.key:16} v{version.version} ({run.modelo})")
            for case in run.casos:
                if not case.ok:
                    print(f"      ✗ {case.nombre}: {case.detalle}")
            failed += 0 if run.ok else 1
        session.rollback()
    print(f"\n{failed} fallaron")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
