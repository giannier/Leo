
# Milestone C — recorrido automático con el modelo real


## C1 — Un instituto configurado desde cero con capas

- ✅ La plantilla sugerida para instituto es certificaciones — certificaciones
- ✅ Perfil, FAQ y pago con titular se guardan
- ✅ El resumen avanza — progreso 41 %, siguiente primera_situacion

## C2 — El tono de un instituto (la queja original)

- **Cliente:** Hola, buenas tardes
  - etapa INICIO → CALIFICAR · embudo NUEVO · tools [enviar_recurso({"clave": "saludo"})→no_disponible]
  - **Leo** (texto): ¡Bienvenido a Escuela Capacita! Buenas tardes 😊 ¿Cuál es su nombre?
- **Cliente:** Me llamo Ana. ¿Qué me ofrecen?
  - etapa CALIFICAR → CALIFICAR · embudo NUEVO · tools [mostrar_opciones({"tipo": "caminos", "texto": "Ana, ¿quiere solo certificar la experiencia que ya tiene, o llevar un programa completo?"})→hecho]
  - **Leo** (botones): Ana, ¿quiere solo certificar la experiencia que ya tiene, o llevar un programa completo? · botones: Solo certificar, Programa completo
  - **Leo** (texto): Ana, ¿quiere solo certificar la experiencia que ya tiene, o llevar un programa completo?
- **Cliente:** Quiero certificar mi experiencia en SIAF, ¿para qué me sirve el certificado?
  - etapa CALIFICAR → CALIFICAR · embudo NUEVO · tools [consultar_conocimiento({"consulta": "¿Para qué sirve el certificado de certificar experiencia en SIAF?"})→no_disponible]
  - **Leo** (texto): No tengo esa información en el negocio. Si usted quiere, puedo derivarle con una persona para que le expliquen para qué le sirve el certificado en SIAF.
- ✅ Habla de certificados, no de productos de tienda — no tengo esa información en el negocio. si usted quiere, puedo derivarle con una persona para que le expliquen para qué le sirve el certificado en siaf.
- ✅ Trata de usted (perfil) — no tengo esa información en el negocio. si usted quiere, puedo derivarle con una persona para que le expliquen para qué 

## C3 — Apagar una regla del rubro

- **Cliente:** ¿Y el de Excel avanzado?
  - etapa CALIFICAR → CALIFICAR · embudo NUEVO · tools [consultar_conocimiento({"consulta": "para qué sirve el certificado de Excel avanzado en Escuela Capacita"})→no_disponible]
  - **Leo** (texto): No tengo esa información sobre el certificado de Excel avanzado. Si usted quiere, puedo derivarle con una persona para que le orienten mejor.
- ✅ La regla apagada sale del prompt y la depuración la muestra

## C4 — Una situación: borrador, prueba, activación

- **Cliente:** Hola, ¿me pueden dar factura?
  - etapa INICIO → CALIFICAR · embudo NUEVO · tools [consultar_conocimiento({"consulta": "Si pueden emitir factura y qué datos necesitan para hacerlo."})→no_disponible]
  - **Leo** (texto): Hola, ¡bienvenido a Escuela Capacita! 😊

No tengo esa información por ahora. Si desea, puedo derivarlo con una persona para que le confirmen si emiten factura y qué datos necesitan.
- **Cliente:** Necesito factura con RUC, por favor
  - etapa CALIFICAR → CALIFICAR · embudo NUEVO · tools []
  - **Leo** (texto): Claro. Por favor, envíeme su *RUC* y *razón social* para registrarlo. La factura llega con la confirmación del pago.
- situaciones: Si piden factura (borrador) d=0.23
- **Cliente:** Buenas, ¿emiten factura?
  - etapa INICIO → CALIFICAR · embudo NUEVO · tools [consultar_conocimiento({"consulta": "Si emiten factura y qué datos se necesitan para emitirla."})→no_disponible]
  - **Leo** (texto): ¡Bienvenido a Escuela Capacita! 😊

Sí, podemos ayudarle con la factura. Por favor, envíeme su *RUC* y *razón social* para registrarlo. La factura llega con la confirmación del pago.
- situaciones: Si piden factura d=0.388
- ✅ Un borrador no entra sin pedirlo — []
- ✅ Probado en el simulador entra y se cumple — Claro. Por favor, envíeme su *RUC* y *razón social* para registrarlo. La factura llega con la confirmación del pago.
- ✅ Activa, entra sola por similitud — ['Si piden factura']

## C5 — Una versión nueva del rubro: evals, publicación y novedad

- evals: ✓ tiene_reglas, ✓ tiene_faqs, ✓ tiene_objeciones, ✓ plantilla_sugerida_existe, ✓ situaciones_sugeridas_en_limites, ✓ reglas_caben_en_su_tope, ✓ regla_apagada_sale_del_prompt, ✓ situacion_gana_sobre_el_rubro, ✓ situacion_siempre_se_cumple, ✓ no_inventa_precios
- ✅ Sin evals no se publica
- ✅ Los evals del borrador pasan con el modelo real
- ✅ Publicada, el negocio ve la novedad — {'version': 2, 'vista': 1, 'reglas': [{'rule_key': 'rubro.horas_lectivas', 'texto': 'Si preguntan cuántas horas tiene un certificado, respóndelo solo con el cat
- ✅ Marcada como vista, desaparece

## C6 — Costo por mensaje

- ✅ Un turno con capas y una situación queda bajo 6 000 tokens de entrada — {'prompt': 2612, 'completion': 84}

## Resumen

- ✅ La plantilla sugerida para instituto es certificaciones — certificaciones
- ✅ Perfil, FAQ y pago con titular se guardan
- ✅ El resumen avanza — progreso 41 %, siguiente primera_situacion
- ✅ Habla de certificados, no de productos de tienda — no tengo esa información en el negocio. si usted quiere, puedo derivarle con una persona para que le expliquen para qué le sirve el certificado en siaf.
- ✅ Trata de usted (perfil) — no tengo esa información en el negocio. si usted quiere, puedo derivarle con una persona para que le expliquen para qué 
- ✅ La regla apagada sale del prompt y la depuración la muestra
- ✅ Un borrador no entra sin pedirlo — []
- ✅ Probado en el simulador entra y se cumple — Claro. Por favor, envíeme su *RUC* y *razón social* para registrarlo. La factura llega con la confirmación del pago.
- ✅ Activa, entra sola por similitud — ['Si piden factura']
- ✅ Sin evals no se publica
- ✅ Los evals del borrador pasan con el modelo real
- ✅ Publicada, el negocio ve la novedad — {'version': 2, 'vista': 1, 'reglas': [{'rule_key': 'rubro.horas_lectivas', 'texto': 'Si preguntan cuántas horas tiene un certificado, respóndelo solo con el cat
- ✅ Marcada como vista, desaparece
- ✅ Un turno con capas y una situación queda bajo 6 000 tokens de entrada — {'prompt': 2612, 'completion': 84}
