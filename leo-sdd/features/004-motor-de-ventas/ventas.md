# Cómo vende Leo: investigación, reglas y resultados

Decisión de Giannier (2026-10-20): el agente debe sondear la necesidad, recomendar con seguridad, resolver
objeciones y cerrar, con un trato que dé confianza, y esa mejora debe valer para **todos** los negocios.
Esta página recoge de dónde salen las reglas, dónde viven en el código y cómo se mide.

## Qué mostró una conversación real

Un cliente real consultó por hosting y terminó interesado en una web con reservas y ventas. En 5 turnos el agente
(plantilla "Venta de productos", sin sondeo) listó precios tres veces, preguntó "¿quieres que te cuente...?" cuatro
veces, nunca pidió nombre ni correo y no propuso un siguiente paso. Además el CRM quedó vacío: la búsqueda del
modelo (`consulta: hosting`, `categoria: hosting`) no coincidía con ninguna categoría (los productos no
tenían), caía al listado de respaldo y eso **no** cuenta como "el cliente vio productos"; el lead nace con esa marca.

## Las técnicas que se adoptaron (y de dónde salen)

| Regla del prompt (`SALES_PLAYBOOK`) | Técnica | Fuente |
|---|---|---|
| 1. Sondea antes de ofrecer, una pregunta abierta a la vez; con 2 respuestas ya recomienda | Venta consultiva, SPIN (situación, problema, implicación, beneficio) de Neil Rackham. Quienes cierran ventas grandes preguntan más por las *implicaciones*, no solo más | [Supademo](https://supademo.com/blog/spin-selling), [Gong](https://www.gong.io/?p=12402) |
| 2. Una recomendación con el porqué, sin recitar el catálogo | Recomendar con criterio, no listar | práctica consultiva |
| 3. Habla en firme, sin "quizás", y solo con hechos | Un tono seguro se percibe como confianza; la seguridad solo funciona si es honesta | [Jeff Shore](https://jeffshore.com/2015/11/how-to-build-customer-confidence-through-advanced-communication/), [MIT Sloan](https://sloanreview.mit.edu/article/speaking-to-customers-in-uncertain-times) |
| 4. Cada mensaje termina con un siguiente paso concreto | Cierre con siguiente paso: resumen del valor y acción concreta | [Second Nature](https://secondnature.ai/closing-techniques/) |
| 5. Objeciones: reconocer, explorar qué pesa más, responder a eso, volver al paso | LAER (escuchar, reconocer, explorar, responder); la primera objeción cerca del cierre casi siempre es superficial | [Sifthub](https://www.sifthub.io/blog/objection-handling-template), [ClickUp](https://clickup.com/blog/objection-handling/) |
| 6. Señal de compra: resumir en una línea y proponer cerrar | Cierre asumido y por alternativas; mal usado y temprano resulta presionante | [Salesmate](https://www.salesmate.io/blog/sales-closing-techniques-tips-you-just-cant-miss), [Shopify](https://www.shopify.com/blog/closing-a-sale) |
| 7. Cercanía, nombre, mensajes cortos, pedir datos con motivo | Conversación bidireccional por WhatsApp, tono personal y profesional | [Klaviyo](https://academy.klaviyo.com/en-us/best-practices/best-practices-for-getting-started-with-whatsapp), [Interakt](https://www.interakt.shop/blog/conversational-commerce-on-whatsapp-best-practices-from-interakt) |

Casi todas las fuentes son blogs de proveedores o de formación comercial: son práctica difundida, no estudios
controlados. Lo que más respaldo tiene (Rackham) es lo que va primero.

## Lo que el agente nunca hace

Inventar casos de éxito, clientes, plazos o cifras; crear escasez o urgencia falsas; mencionar descuentos que el
negocio no cargó (ni decir "no hay promoción"); derivar a una persona solo por una objeción de precio; reabrir una
decisión ya tomada; ofrecer lo que no está en sus herramientas. Son las reglas 2, 4 y 6 de la plataforma y el
límite honesto de la técnica: la persuasión funciona sobre información real.

## Dónde vive en el código

- `SALES_PLAYBOOK` y `SERVICE_ONLY_PLAYBOOK` en `backend/app/agent/prompts/stage.py`: sección **venta** del prompt
  (tope 540 tokens, unos 430 reales; ≈ +10 % por turno). Va en todas las etapas hasta el cobro y se omite en
  verificación, cerrado y derivado.
- **El objetivo del negocio ahora se lee.** `agent_configs.objective` (vender, atender, ambos) llegaba solo al
  constructor antiguo del prompt y el motor de ventas lo ignoraba. Ahora `Behavior.objetivo` decide: `atender`
  pone un texto de "resuelve dudas, no presiones"; `vender` y `ambos` llevan el playbook completo.
- Textos de etapa: `IDENTIFICAR` de `servicios` y `productos`, `ASESORIA` base, seguimientos y el mensaje de
  derivación (`process/templates/`, espejados en `plantillas.md`).
- `buscar_productos`: si la categoría que adivinó el modelo no coincide, vuelve a buscar por el nombre (antes la
  categoría anulaba el nombre y el CRM no se enteraba).
- CRM: una derivación a asesor (`motivo = asesoria`) deja el lead en "Interesado".

## Cómo se mide

`backend/evals/test_sales_quality.py` (`make evals`): 4 conversaciones por plantilla (cafetería con reservas,
objeción de precio, cliente decidido, cliente que no sabe qué necesita), 42 comprobaciones sobre las respuestas
(pregunta antes de ofrecer, recomienda, no ofrece "te cuento" en vez de contar, sin palabras de duda, cierra con
un paso, avanza ante la señal de compra, sin montos inventados). Son patrones, no juicio: miden lo que un cambio de
prompt puede romper o arreglar, y los diálogos completos quedan en el informe para leerlos.

| Medición | Resultado |
|---|---|
| Antes del cambio | 57 % |
| Solo con el bloque de venta en el prompt (6 corridas, mismo modelo `gpt-5.4-mini`) | 81 % a 93 % |
| Con los textos de las plantillas ya publicados como versión 3 (3 corridas) | 86 %, 88 % y 98 % |
| Flujos de venta existentes (24 conversaciones, 2 corridas) | 24/24 y 24/24 |

Los textos de etapa viven en la base de datos como reglas versionadas (017): cambiarlos en el código no basta,
se publican como versión nueva con una migración (la `0048`, versión 3 de `productos`, `servicios` y
`certificaciones`). Cada negocio ve las reglas nuevas como novedades y puede apagar las que no quiera.

El umbral es 75 % porque el modelo varía de una corrida a otra.

## Cómo seguirá mejorando (pendiente de ratificar: Fases 3 y 4 de la 017)

Un modelo no aprende solo de las conversaciones. Se mejora con un ciclo: registrar el resultado (ganada, perdida,
objeción), analizarlo en conjunto, convertirlo en cambios de las plantillas y rubros de la plataforma, probarlos con
estos evals y publicarlos con revisión humana. Usar conversaciones de varios negocios, aun anonimizadas, toca datos
personales de terceros (Ley 29733): requiere decisión y consentimiento en los términos de cada cliente.
