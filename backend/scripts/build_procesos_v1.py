"""Write `alembic/data/procesos_v1.json`: the stage texts of the code templates as rules.

Run once for migration 0030 (017, TASK-460). The migration reads the frozen file,
not the code, so later edits to `process/templates/*` never rewrite history.

    uv run python -m scripts.build_procesos_v1
"""

from __future__ import annotations

import json
from pathlib import Path

from app.process.rule_split import rule_keys, split_rules
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage

OUT = Path(__file__).resolve().parents[1] / "alembic" / "data" / "procesos_v1.json"


def build() -> dict[str, object]:
    procesos = []
    for key, template in TEMPLATES.items():
        rules = []
        for stage in Stage:
            texts = split_rules(template.stage(stage).instrucciones)
            for rule_key, text in zip(rule_keys(stage.value, texts), texts, strict=True):
                rules.append({"rule_key": rule_key, "etapa": stage.value, "texto": text})
        procesos.append({"key": key, "nombre": template.nombre, "reglas": rules})
    note = "Generado por scripts/build_procesos_v1.py; no editar a mano."
    return {"_nota": note, "procesos": procesos}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"escrito {OUT}")
