# Clarificaciones — 020 Proceso universal

Decisiones que necesita Giannier antes de construir (F0). Cada una trae la recomendación y por qué.

## D1: El tipo «producto» o «servicio» como dato del ítem
**Recomendación:** sí, un campo explícito en cada ítem del catálogo. Hoy se deduce de la plantilla; con un solo proceso hace
falta saberlo por ítem (un servicio no tiene stock ni envío; un producto físico sí).
**Estado:** aprobada como se recomienda (Giannier, 2026-10-28: «acepto todo, a excepción del punto 5»).

## D2: De dónde se eligen los ítems de un paquete
**Opciones:** (a) de cualquier ítem del negocio (hoy); (b) solo de una categoría (nuevo: «paquete de 3 herramientas» elige
solo entre herramientas); (c) también nombres libres (hoy: «se certifica cualquier tema»).
**Recomendación:** las tres, elegidas por paquete; (b) por defecto cuando el negocio indica la categoría.
**Estado:** aprobada como se recomienda (Giannier, 2026-10-28: «acepto todo, a excepción del punto 5»).

## D3: Las reglas de rubro de hoy pasan a ser situaciones editables
**Opciones:** (a) mantenerlas como reglas que solo se apagan; (b) convertirlas en situaciones sugeridas, editables.
**Recomendación:** (b), con novedades cuando Zentek360 las mejore. Es lo que pidió: punto de partida que el negocio cambia.
**Costo:** el negocio que edita una situación deja de recibir automáticamente la mejora de esa situación; la recibe como
novedad para aceptar.
**Estado:** aprobada como se recomienda (Giannier, 2026-10-28: «acepto todo, a excepción del punto 5»).

## D4: Caminos para cualquier rubro
**Recomendación:** sí, opcionales: una pregunta inicial con caminos («¿comprar o alquilar?» en inmobiliaria; «¿certificar o
estudiar?» en educación). Hoy solo los usa certificaciones.
**Estado:** aprobada como se recomienda (Giannier, 2026-10-28: «acepto todo, a excepción del punto 5»).

## D5: Costo de las pruebas con el modelo real (costo del LLM)
**Estimado:** ~US$0,6–0,9 por pasada completa de la batería universal con la caché; dos pasadas por umbral; ~US$1,5–2 por
fase con pruebas (F2 y F5, y una al publicar cada plantilla de rubro). Total de la feature: ~US$6–8.
**Recomendación:** aprobar el total, con aviso antes de cada pasada y el total acumulado en cada reporte (como hasta ahora).
**Estado:** **no aprobada por ahora** (Giannier, 2026-10-28): no se usa la API de OpenAI hasta que apruebe el presupuesto.
Mientras tanto: (1) cada fase se prueba con la suite automática, que no llama a ningún modelo; (2) para el texto del proceso
(F2 y F3) se simulan conversaciones con Claude haciendo de agente, a cuenta del plan de Giannier, lo que comprueba que el
proceso lleva al paso y la herramienta correctos y que no hay contradicciones, pero **no reemplaza** la prueba con el modelo
que atiende (gpt-5.4-mini). (3) La constitución exige evals antes de cambiar el prompt en producción: el cambio de un negocio
al proceso universal en producción (F5) espera ese presupuesto.

## D6: Orden de migración en producción
**Hoy:** 2 negocios: uno en productos (rubro «otros», activo) y Nayu en servicios (b2b, inactivo).
**Recomendación:** primero Nayu (no atiende clientes todavía), luego el activo, cada uno tras pasar las pruebas y con el
visto bueno de Giannier.
**Estado:** aprobada como se recomienda (Giannier, 2026-10-28: «acepto todo, a excepción del punto 5»).

## D7: «Solo informar» como forma de cierre
**Recomendación:** sí, para lo que arma una persona (un programa a medida, un proyecto): el agente responde, entiende y pasa
a un asesor con resumen. Hoy existe solo como «camino de asesoría».
**Estado:** aprobada como se recomienda (Giannier, 2026-10-28: «acepto todo, a excepción del punto 5»).

## D8: Quitar el límite de 3 servicios por pedido
**Recomendación:** sí. Era una regla de la plantilla servicios; un negocio que vende varios servicios no debería tener un
tope que no eligió. Si alguno lo necesita, se configura.
**Estado:** aprobada como se recomienda (Giannier, 2026-10-28: «acepto todo, a excepción del punto 5»).
