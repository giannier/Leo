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

## C-01: Cómo se cumple D3 sin quitarle espacio a las situaciones del negocio (lectura conservadora, 2026-10-28)
**Situación:** D3 pide que las reglas de rubro sean un punto de partida que el negocio cambia. Convertirlas en situaciones
«siempre» las haría ocupar primero los lugares del selector de situaciones (5 por mensaje): la plantilla de educación trae 5
reglas, y las situaciones propias del negocio no entrarían nunca.
**Decisión:** las reglas del rubro siguen siendo reglas (bloque RUBRO, con su tope de tokens), pero el negocio puede
**escribirlas con sus palabras** (`tenant_rule_switches.texto_propio`), además de apagarlas como ya podía. Una mejora que
Zentek360 publique llega como novedad: si el negocio tiene su propio texto, lo conserva hasta que acepte la mejora. Las
**situaciones sugeridas** (las que dicen «cuándo aplica») sí se copian como situaciones del negocio.
**Registrado por:** Claude — lectura conservadora de D3, por ratificar.

## C-02: Qué hace «aplicar la plantilla» con lo que el negocio ya tiene (lectura conservadora, 2026-10-28)
**Situación:** un negocio puede haber creado situaciones desde las sugeridas de su rubro antes de esta feature (sin clave), o
haber elegido ya sus caminos, sus datos o su forma de pago.
**Decisión:** aplicar nunca pisa nada. Una situación que ya vino de la plantilla (por su clave o, si es anterior, por su título)
cuenta como «ya estaba» y recibe la clave; las opciones del proceso (caminos, datos del cliente, pago) solo llenan las que el
negocio no definió, y una que su proceso no acepte se deja fuera. Aplicar dos veces solo trae lo que falta. Cada situación
creada y el cambio de opciones quedan en el historial y se pueden deshacer; escribir una regla con sus palabras también.
**Registrado por:** Claude — lectura conservadora, por ratificar.

## C-03: Cómo acepta el negocio una mejora (lectura conservadora, 2026-10-28)
**Situación:** Zentek360 publica una versión nueva de la plantilla de un rubro.
**Decisión:** las novedades se ven elemento por elemento: «nueva» (no la tiene) o «mejorada» (cambió su texto). Aceptar una
mejorada reemplaza el texto de su copia y conserva su estado, archivos y etapas; si la situación pide datos (por ejemplo,
«desde cuántas personas es grupo») hay que darlos otra vez o queda en borrador. Marcar las novedades como vistas deja todo como
está. Para una regla del rubro escrita con sus palabras, aceptar la mejora es volver al texto de la plantilla.
**Registrado por:** Claude — lectura conservadora, por ratificar.

## C-04: Lo que corrigió la simulación T2.7 (lectura conservadora, 2026-10-28)
**Situación:** las conversaciones simuladas ([simulacion-t27.md](simulacion-t27.md)) mostraron fallas que no son de una
plantilla sino del sistema o del contenido.
**Decisiones:**
- **Reglas de reserva de siete rubros** (0074): condicionadas a tener agenda. Es contenido nuevo de rubro y cambia el prompt de
  todos los negocios de esos rubros, también en las plantillas antiguas: entra a producción solo con la pasada del modelo real.
- **Elecciones de un paquete cerrado:** un nombre corto vale si señala un solo producto elegible y no trae un número o una
  letra que ese nombre no tenga. Un paquete con temas libres guarda lo que escribió el cliente.
- **Búsqueda:** si la frase entera no coincide, se busca por raíces sin tildes; solo es «coincidencia» si un producto tiene
  todas las palabras. Revisa hasta 500 productos activos.
- **Datos completos (solo universal):** se pide confirmar el pedido; el pago va con el «sí». No agrega llamadas al modelo.
- **Precio de lo que arma una persona:** «precio con un asesor», nunca «S/ 0.00».
- **Agenda:** `desde` admite una hora.
- **COBRO del universal:** solo las líneas de la forma de pago del negocio.
- **Situaciones sugeridas con etapas y «siempre»:** el saludo de educación entra siempre, solo en INICIO; ocupa un lugar del
  selector solo en el primer mensaje (afina C-01).
**Registrado por:** Claude — lectura conservadora, por ratificar.

