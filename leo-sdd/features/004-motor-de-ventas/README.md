# 004 v2 — Motor de ventas

Convierte a Leo en un vendedor que corre solo: el cliente configura con formularios (estilo Ventor), el
sistema calcula la etapa de cada conversación, el agente arma pedidos con precios reales, recibe el
comprobante y una persona lo confirma desde Pedidos.

## Contenido

| Archivo | Para qué |
|---|---|
| `spec.md` | Qué y por qué: 8 historias, 31 RF, criterios de aceptación |
| `clarifications.md` | 22 decisiones tomadas por Claude, **por ratificar** |
| `plantillas.md` | El contenido del motor: etapas, cálculo, herramientas por etapa y textos por defecto de las 3 plantillas |
| `plan.md` | Arquitectura, pipeline del turno, módulos, riesgos y fases |
| `data-model.md` | Migraciones 0014–0020 |
| `contracts/api.md` | Endpoints, contrato de las herramientas, adaptador de YCloud, jobs y notificaciones |
| `tasks.md` | TASK-401…449 en 5 fases, con 4 milestones tuyos (🧑) |
| `referencias/engine_referencia.py` | Motor de etapas de referencia con 34 casos que pasan (base de TASK-410) |
| `referencias/capacita_v21/` | Código real de Capacita v21 (cálculo de etapa, formateador, 25 pruebas) para portar |

## Dónde va en el repo de Leo

```
leo-sdd/features/004-motor-de-ventas/        ← esta carpeta completa
leo-sdd/features/_archivo/004-tools-v1/      ← la spec 004 vieja (lo hace TASK-401)
```

## Antes de empezar (tú)

1. Lee `clarifications.md` y cambia lo que no te convenza. Lo demás ya lo asume.
2. Haz el merge de la rama `task-025-hardening-and-coverage` a `main` y empieza la rama `feat/004-motor-de-ventas` desde ahí.

## Prompt para Claude Code

```text
Vamos a implementar la feature leo-sdd/features/004-motor-de-ventas/. Es la 004 del MVP (no la Fase 2 de 011–016).

1. Lee en este orden: README.md, spec.md, clarifications.md, plantillas.md, plan.md, data-model.md,
   contracts/api.md, tasks.md y referencias/capacita_v21/LEEME.md. Lee también ESTADO.md y CLAUDE.md.
2. Empieza por TASK-401: concilia la spec con el código real y escribe conciliacion.md. No cambies
   código en esa task. Cuando termines, detente y muéstrame el resumen.
3. Después sigue tasks.md en orden respetando "Depends on". Una task = un commit con la convención
   feat(módulo): ... [TASK-XXX]. Marca [x] en tasks.md al terminar cada una.
4. Gate de cada task: ruff, mypy y los tests del módulo en verde. Tests de aislamiento con cada tabla nueva.
5. Si el repo resuelve algo distinto a data-model.md o contracts/api.md (tipo de PK, prefijo de API,
   estructura del frontend), manda el repo: adáptalo y anótalo en conciliacion.md.
6. Si la documentación de YCloud contradice contracts/api.md §8, manda YCloud: anótalo y sigue.
7. En las tasks con 🧑 detente: prepara el guion del milestone y espera mi aprobación.
8. No agregues dependencias nuevas sin preguntarme. No leas .env ni muestres secretos.
```

## Qué vas a poder probar y cuándo

| Después de | Pruebas en el simulador |
|---|---|
| Fase A (TASK-423) | Capacita: saludo, calificación con botones, paquetes con flyer, anuncio |
| Fase B (TASK-433) | Pedido completo con comprobante y confirmación desde Pedidos |
| Fase C (TASK-442) | Un tercero configura un negocio nuevo en 30 minutos o menos |
| Fase E (TASK-449) | Capacita, una tienda y Zentek360 aprobados → PR |

Luego sigue el VPS (staging con número real), que valida el p95 y el TASK-209 pendiente.
