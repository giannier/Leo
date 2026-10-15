# Milestone B — recorrido automático con el modelo real

> Generado el 2026-10-14 por `backend/tests/integration/test_zz_milestone_b_live.py` con `gpt-5.4-mini`
> (perfil `openai`), sobre el simulador y la API de Pedidos. Datos ficticios. Las tools muestran los
> datos personales enmascarados, como el panel de depuración. Resumen y fallas corregidas: `B.md`, sección 7.


## Escenario 4 — Capacita, paquete de 5 con 2 temas

- **Cliente:** Hola
  - etapa INICIO → CALIFICAR · embudo NUEVO · tools [enviar_recurso({"clave": "saludo"})→no_disponible]
  - **Leo** (texto): ¡Hola! 😊 Soy Leo de Escuela Capacita. ¿Cómo te llamas?
- **Cliente:** Me llamo Ana
  - etapa CALIFICAR → CALIFICAR · embudo NUEVO · tools [registrar_datos_cliente({"nombres": "***"})→hecho; mostrar_opciones({"tipo": "caminos", "texto": "Ana, ¿quieres solo certificar la experiencia que ya tienes, o llevar un programa completo?)→hecho]
  - **Leo** (botones): Ana, ¿quieres solo certificar la experiencia que ya tienes, o llevar un programa completo? · botones: Solo certificar, Programa completo
- **Cliente:** [toca «Solo certificar»]
  - etapa CALIFICAR → IDENTIFICAR · embudo ATENDIDO · tools [elegir_camino({"camino": "certificacion"})→omitido; mostrar_opciones({"tipo": "categorias", "texto": "Perfecto, Ana. ¿Qué tema necesitas certificar?"})→hecho]
  - **Leo** (lista): Perfecto, Ana. ¿Qué tema necesitas certificar?
  - **Leo** (texto): Perfecto, Ana. ¿Qué tema necesitas certificar?
- **Cliente:** Necesito certificarme en SIAF
  - etapa IDENTIFICAR → IDENTIFICAR · embudo ATENDIDO · tools [buscar_productos({"consulta": "SIAF", "categoria": "certificacion", "solo_paquetes": false})→hecho; mostrar_opciones({"tipo": "productos", "filtro": "paquetes", "texto": "Ana, la certificación individual de SIAF cuesta S/ 120.00. Te mues)→hecho]
  - **Leo** (botones): Ana, la certificación individual de SIAF cuesta S/ 120.00. Te muestro los paquetes disponibles por si quieres ahorrar con más certificaciones. · botones: Paquete 5 certifica…, Paquete 10 certific…, Paquete 2 certifica…
  - **Leo** (texto): Ana, la certificación individual de SIAF cuesta S/ 120.00. Te muestro los paquetes por si quieres ahorrar con más certificaciones.
- **Cliente:** [toca «Paquete 5 certifica…»]
  - etapa IDENTIFICAR → PEDIDO · embudo NEGOCIACION · tools [gestionar_pedido({"accion": "agregar", "producto_id": "c7f0693e-88a8-4f05-9976-a460e20d6a71"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Listo, Ana. Ya quedó agregado el *Paquete 5 certificaciones* por *S/ 500.00*. Faltan *5 elecciones* por definir. ¿Qué otros temas quieres certificar?
- **Cliente:** Quiero ese, con SIAF y Excel avanzado
  - etapa PEDIDO → PEDIDO · embudo NEGOCIACION · tools [gestionar_pedido({"accion": "elegir", "seleccion": "SIAF, Excel avanzado"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Quedó registrado, Ana: *SIAF* y *Excel avanzado*. Te faltan *3* más para completar el paquete 😊 ¿Qué otros temas quieres certificar?
- ✅ E4.1 paquete de 5 con 2 temas queda en PEDIDO con faltan 3 — faltan 3, etapa PEDIDO
- **Cliente:** SEACE, Power BI y Gestión de museos
  - etapa PEDIDO → DATOS · embudo NEGOCIACION · tools [gestionar_pedido({"accion": "elegir", "seleccion": "SEACE, Power BI, Gestión de museos"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Quedó registrado, Ana: *SEACE, Power BI y Gestión de museos*. Tu paquete ya está completo con *SIAF, Excel avanzado, SEACE, Power BI y Gestión de museos*. ¿Me compartes tu DNI para seguir?
- ✅ E4.2 con 5 temas el pedido está completo y pasa a DATOS; el tema libre se acepta — completo True, etapa DATOS, selecciones [['SIAF', 'Excel avanzado', 'SEACE', 'Power BI', 'Gestión de museos']]
- **Cliente:** Mi DNI es 12345678 y me llamo Ana
  - etapa DATOS → DATOS · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "dni": "****5678"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Gracias, Ana. Me faltan tus *apellidos*, *correo* y *teléfono* para registrar tu pedido y enviarte la información de pago. ¿Me los compartes?
- ✅ E4.3 registra lo válido y pide solo lo que falta, sin métodos de pago aún — faltan ['apellidos', 'correo', 'telefono']
- ✅ Datos personales enmascarados en el debug (DNI)
- **Cliente:** Quispe Rojas, ana@example.com, 987654321
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"apellidos": "***", "correo": "a***@example.com", "telefono": "****4321"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): ¡Gracias, Ana! Ya están todos tus datos. Ahora sigo con el siguiente paso de tu pedido.
- **Cliente:** Sí, pásame los métodos de pago
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Yape*
Yape al 987654321 a nombre de Escuela Capacita. Cuando pagues, envía la foto del comprobante por aquí.
  - **Leo** (texto): Perfecto, Ana. Tu total es *S/ 500.00*. Cuando hagas el pago, envíame por aquí la foto del comprobante, por favor.
- ✅ E4.4-5 datos completos sin pedir dirección; envía los métodos de pago — etapa COBRO
- **Cliente:** ¿Cuáles eran las cuentas?
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→omitido] · pedido borrador 500.00
  - **Leo** (texto): Revisa el mensaje anterior, Ana. Cuando hagas el pago, envíame por aquí la foto del comprobante, por favor.
- ✅ E4.6 no reenvía los métodos de pago (una vez por pedido)

## Escenario 5 — comprobante y confirmación

- **Cliente:** [imagen no-comprobante.png]
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [] · pedido borrador 500.00
  - **Leo** (texto): Gracias, Ana. Esa imagen no parece ser un comprobante de pago. ¿Me envías la foto del comprobante, por favor?
- ✅ E5.1 una foto que no es comprobante no crea comprobante ni cambia la etapa — etapa COBRO
- **Cliente:** [imagen comprobante-monto.png]
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [gestionar_pedido({"accion": "ver"})→hecho] · pedido por_confirmar 500.00
  - **Leo** (texto): Gracias, Ana. Tu pedido está completo: *Paquete 5 certificaciones* con *SIAF, Excel avanzado, SEACE, Power BI y Gestión de museos*. Total: *S/ 500.00*. Tu comprobante está en revisión y te avisaremos apenas se confirme.
- ✅ E5.2 comprobante con otro monto: pedido Por confirmar, marcado y no rechazado — estado por_confirmar, observaciones ['monto_distinto', 'destinatario_no_verificado']
- ✅ E5.2 el agente no dice que el pago está confirmado — Gracias, Ana. Tu pedido está completo: *Paquete 5 certificaciones* con *SIAF, Excel avanzado, SEACE, Power BI y Gestión de museos*. Total: *S/ 500.00*. Tu compr
- ✅ E5.3 detalle con ítems, selecciones, comprobante leído, observaciones e historial — monto leído 450.00
- ✅ E5.4 rechazar devuelve el pedido a Borrador y avisa — {"estado":"borrador","aviso_cliente":"enviado"}
- **Cliente:** [imagen comprobante-ok.png]
  - etapa VERIFICACION → VERIFICACION · embudo EN_VERIFICACION · tools [gestionar_pedido({"accion": "ver"})→hecho] · pedido por_confirmar 500.00
  - **Leo** (texto): Ana, tu pedido está completo: *Paquete 5 certificaciones* con *SIAF, Excel avanzado, SEACE, Power BI y Gestión de museos*. Total: *S/ 500.00*. Tu comprobante sigue en revisión y te avisaremos apenas se confirme.
- ✅ E5.5 comprobante nuevo correcto: Por confirmar, sin marca de monto — observaciones ['destinatario_no_verificado']
- ✅ E5.6 confirmar cambia el estado y avisa; la segunda confirmación es 409 (5c) — {'estado': 'confirmado', 'aviso_cliente': 'enviado'} / segunda 409
- **Cliente:** ¿Cuándo me llegan?
  - etapa CERRADO → CERRADO · embudo GANADO · tools [consultar_conocimiento({"consulta": "tiempo de entrega de certificados y cómo los recibe el cliente después del pago confirmado"})→no_disponible; derivar_a_humano({"motivo": "sin_respuesta", "resumen": "La clienta pregunta cuándo le llegan sus certificados después del pago confirmad)→no_disponible] · pedido confirmado 500.00
  - **Leo** (texto): No tengo ese dato confirmado, Ana. ¿Quieres que te derive con una persona para que te indique el tiempo exacto de entrega?
- ✅ E5.7 en CERRADO no pide comprobante ni reenvía métodos — No tengo ese dato confirmado, Ana. ¿Quieres que te derive con una persona para que te indique el tiempo exacto de entrega?
- ✅ E5.8 En curso y Completado funcionan en orden
- ✅ Los pedidos de prueba solo salen con la casilla

## T1 — dos polos, delivery, comprobante, confirmación

- **Cliente:** Hola
  - etapa INICIO → IDENTIFICAR · embudo ATENDIDO · tools [enviar_recurso({"clave": "saludo"})→no_disponible]
  - **Leo** (texto): ¡Hola! Soy Leo de *Tienda Demo* 😊 ¿En qué te puedo ayudar?
- **Cliente:** Quiero 2 polos
  - etapa IDENTIFICAR → DATOS · embudo NEGOCIACION · tools [buscar_productos({"consulta": "polos", "categoria": "polos"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "2c71d982-1668-49e0-a2ba-a89e004cb1c3", "cantidad": 2})→hecho] · pedido borrador 35.00
  - **Leo** (texto): Listo, agregué *2 Polo básico* al pedido. Quedó en *S/ 35.00* con la promo *2x1 en polos* 😊 ¿Deseas algo más?
- ✅ T1.2 2 polos: subtotal 70.00, descuento 35.00, total 35.00 — 70.00 / 35.00 / 35.00
- **Cliente:** Soy Ana Quispe, mi número es 987654321
  - etapa DATOS → DATOS · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "telefono": "****4321"})→hecho] · pedido borrador 35.00
  - **Leo** (texto): Gracias, Ana. Ya tengo tu nombre y número. Me faltan tu *dirección* y *zona de envío* para coordinar el delivery, ¿me las compartes?
- ✅ T1.3 no pregunta delivery o recojo (solo delivery); pide zona y dirección — faltan ['direccion', 'zona_envio']
- **Cliente:** Delivery en Miraflores, Av. Larco 123
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"tipo_entrega": "delivery", "direccion": "***", "zona_envio": "Miraflores"})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Ya están todos los datos. Sigue con el siguiente paso del pedido.
- **Cliente:** Sí, pásame cómo pagar
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí.
  - **Leo** (texto): Perfecto, ya te envié los métodos de pago. Tu total es *S/ 43.00* y cuando pagues, envíame por aquí la foto del comprobante, por favor.
- ✅ T1.4 Miraflores: envío 8.00 y total 43.00 — envío 8.00, total 43.00
- ✅ El agente dice el total del pedido (43.00), no uno propio — Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí. Perfecto, ya te envié los métodos de pago. Tu total es *
- **Cliente:** [imagen comprobante-ok.png]
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [gestionar_pedido({"accion": "ver"})→hecho] · pedido por_confirmar 43.00
  - **Leo** (texto): Gracias, Ana. Tu pedido está en revisión y apenas se confirme te avisamos. Tu pedido es *2 Polo básico*, subtotal *S/ 70.00*, descuento *S/ 35.00* por la promo 2x1, envío *S/ 8.00* y total *S/ 43.00*.
- ✅ T1.6 comprobante: Por confirmar con 'El monto no coincide' (la captura dice 500) — ['monto_distinto', 'destinatario_no_verificado']
- **Cliente:** Gracias
  - etapa CERRADO → CERRADO · embudo GANADO · tools [] · pedido confirmado 43.00
  - **Leo** (texto): ¡De nada, Ana! Si luego quieres agregar algo más a tu pedido, dime qué necesitas.
- ✅ T1.8 confirmar cierra la venta (CERRADO)

## T1b — envío gratis

- **Cliente:** Hola, quiero 2 mochilas
  - etapa INICIO → DATOS · embudo NEGOCIACION · tools [enviar_recurso({"clave": "saludo"})→no_disponible; buscar_productos({"consulta": "mochilas", "categoria": "mochilas"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "b6f25c7a-5d27-4e23-87e4-92e0712ee2f0", "cantidad": 2})→hecho] · pedido borrador 240.00
  - **Leo** (texto): ¡Hola, Ana! Soy Leo de Tienda Demo 😊

Ya quedó en tu pedido *2 Mochila ejecutiva* a *S/ 120.00 c/u*.
*Subtotal:* S/ 240.00
*Descuento:* S/ 0.00
*Envío:* S/ 0.00
*Total:* S/ 240.00

¿Quieres que te ayude con el envío?
- **Cliente:** Ana Quispe, 987654321, Miraflores, Av. Larco 123
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "telefono": "****4321", "zona_envio": "Miraflores", "direccion": "***"})→hecho] · pedido borrador 240.00
  - **Leo** (texto): ¡Gracias, Ana! Ya quedó todo listo con tu dirección y zona de envío.

¿Quieres que te comparta los métodos de pago?
- ✅ T1b 2 mochilas (240.00): envío 0.00 — envío 0.00, total 240.00

## T2 — rechazo y corrección

- **Cliente:** Hola, quiero 2 polos
  - etapa INICIO → DATOS · embudo NEGOCIACION · tools [enviar_recurso({"clave": "saludo"})→no_disponible; buscar_productos({"consulta": "polos", "categoria": "polos"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "2c71d982-1668-49e0-a2ba-a89e004cb1c3", "cantidad": 2})→hecho] · pedido borrador 35.00
  - **Leo** (texto): Hola, soy Leo de Tienda Demo 😊

Te quedó *Polo básico x2* en tu pedido:
Subtotal: S/ 70.00
Descuento: S/ 35.00
Total: S/ 35.00

¿Quieres que te ayude con el envío?
- **Cliente:** Soy Ana Quispe, 987654321, delivery en Miraflores, Av. Larco 123
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "telefono": "****4321", "tipo_entrega": "delivery", "direccion": "***", "zona_envio": "Miraflores"})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Perfecto, Ana. Ya tengo todo para tu delivery en Miraflores. ¿Quieres que te comparta los métodos de pago?
- **Cliente:** Sí, pásame cómo pagar
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí.
  - **Leo** (texto): Listo, ya te envié los métodos de pago. Tu total es *S/ 43.00* y cuando pagues, envíame por aquí la foto del comprobante, por favor.
- **Cliente:** [imagen comprobante-ok.png]
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [gestionar_pedido({"accion": "ver"})→hecho] · pedido por_confirmar 43.00
  - **Leo** (texto): Disculpa, no quiero darte el nombre equivocado de un producto. Déjame comunicarte con una persona del negocio para confirmártelo bien.
- **Cliente:** Pásame de nuevo las cuentas
  - etapa VERIFICACION → VERIFICACION · embudo EN_VERIFICACION · tools [enviar_metodos_pago({})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí.
  - **Leo** (texto): Tu total es *S/ 43.00*. Cuando pagues, envíame por aquí la captura del comprobante, por favor.
- ✅ T2.2 tras un rechazo reenvía los métodos una vez — etapa VERIFICACION
- **Cliente:** Mejor agrégame una gorra
  - etapa VERIFICACION → COBRO · embudo CUENTAS_ENVIADAS · tools [buscar_productos({"consulta": "gorra", "categoria": "accesorios"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "00c64aeb-d2d7-42da-b362-3fbd0ecafada", "cantidad": 1})→hecho] · pedido borrador 68.00
  - **Leo** (texto): Listo, ya agregué la *gorra urbana* a tu pedido.

Quedó así:
• 2 polos básicos
• 1 gorra urbana

Total: *S/ 68.00* con envío a Miraflores.
- ✅ T2.3 cambiar el pedido saca al cliente de VERIFICACION — etapa COBRO

## T3 — contra entrega

- **Cliente:** Hola, quiero una mochila y una gorra
  - etapa INICIO → IDENTIFICAR · embudo ATENDIDO · tools [enviar_recurso({"clave": "saludo"})→no_disponible; buscar_productos({"consulta": "mochila", "categoria": "mochilas"})→hecho; buscar_productos({"consulta": "gorra", "categoria": "gorras"})→hecho]
  - **Leo** (texto): ¡Hola, Ana! Soy Leo de Tienda Demo 😊

Sí tenemos *mochila ejecutiva* a S/ 120.00 y *gorra urbana* a S/ 25.00. ¿Quieres que te los agregue al pedido?
- (el agente preguntó antes de agregar; el cliente responde «sí»)
- **Cliente:** Sí
  - etapa IDENTIFICAR → DATOS · embudo NEGOCIACION · tools [buscar_productos({"consulta": "mochila"})→hecho; buscar_productos({"consulta": "gorra"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "b6f25c7a-5d27-4e23-87e4-92e0712ee2f0", "cantidad": 1})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "00c64aeb-d2d7-42da-b362-3fbd0ecafada", "cantidad": 1})→hecho] · pedido borrador 145.00
  - **Leo** (texto): Listo, ya quedó en tu pedido: *mochila ejecutiva* x1 y *gorra urbana* x1. Total: *S/ 145.00*. ¿Deseas algo más?
- **Cliente:** Ana Quispe, 987654321, delivery al Callao, Jr. Unión 45
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "telefono": "****4321", "tipo_entrega": "delivery", "direccion": "***", "zona_envio": "Callao"})→hecho; gestionar_pedido({"accion": "ver"})→hecho] · pedido borrador 157.00
  - **Leo** (texto): ¡Gracias, Ana! Ya quedó todo listo: *mochila ejecutiva* x1 y *gorra urbana* x1. Subtotal *S/ 145.00*, envío al *Callao* *S/ 12.00*, total *S/ 157.00*. ¿Te comparto ahora los métodos de pago?
- ✅ T3.2 145.00 + Callao 12.00 = 157.00 — envío 12.00, total 157.00, modalidad contra_entrega
- ✅ T3.3 en contra entrega no manda cuentas — ¡Gracias, Ana! Ya quedó todo listo: *mochila ejecutiva* x1 y *gorra urbana* x1. Subtotal *S/ 145.00*, envío al *Callao* *S/ 12.00*, total *S/ 157.00*. ¿Te comparto ahora los métodos de pago?
- **Cliente:** Sí, confirmo
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [gestionar_pedido({"accion": "confirmar"})→hecho] · pedido por_confirmar 157.00
  - **Leo** (texto): ¡Gracias, Ana! Tu pedido quedó enviado al negocio para revisión. Total: *S/ 157.00* con delivery al *Callao*. En breve te confirmarán por aquí.
- ✅ T3.4 confirmar deja el pedido Por confirmar — etapa VERIFICACION
- **Cliente:** Ok, entonces qué hago
  - etapa COBRO → COBRO · embudo NEGOCIACION · tools [consultar_conocimiento({"consulta": "zonas de delivery disponibles esta semana y alternativas si Callao no está cubierto"})→no_disponible; gestionar_pedido({"accion": "ver"})→hecho] · pedido borrador 157.00
  - **Leo** (texto): Tu pedido está listo, pero *Callao no está disponible esta semana*. Si quieres, puedo ayudarte a cambiar la zona de entrega o derivarte con una persona para revisarlo. ¿Prefieres cambiar la dirección?
- ✅ T3b rechazar contra entrega vuelve a COBRO, sin rechazo vigente — etapa COBRO

## Resumen del checklist

- ✅ E4.1 paquete de 5 con 2 temas queda en PEDIDO con faltan 3 — faltan 3, etapa PEDIDO
- ✅ E4.2 con 5 temas el pedido está completo y pasa a DATOS; el tema libre se acepta — completo True, etapa DATOS, selecciones [['SIAF', 'Excel avanzado', 'SEACE', 'Power BI', 'Gestión de museos']]
- ✅ E4.3 registra lo válido y pide solo lo que falta, sin métodos de pago aún — faltan ['apellidos', 'correo', 'telefono']
- ✅ Datos personales enmascarados en el debug (DNI)
- ✅ E4.4-5 datos completos sin pedir dirección; envía los métodos de pago — etapa COBRO
- ✅ E4.6 no reenvía los métodos de pago (una vez por pedido)
- ✅ E5.1 una foto que no es comprobante no crea comprobante ni cambia la etapa — etapa COBRO
- ✅ E5.2 comprobante con otro monto: pedido Por confirmar, marcado y no rechazado — estado por_confirmar, observaciones ['monto_distinto', 'destinatario_no_verificado']
- ✅ E5.2 el agente no dice que el pago está confirmado — Gracias, Ana. Tu pedido está completo: *Paquete 5 certificaciones* con *SIAF, Excel avanzado, SEACE, Power BI y Gestión de museos*. Total: *S/ 500.00*. Tu compr
- ✅ E5.3 detalle con ítems, selecciones, comprobante leído, observaciones e historial — monto leído 450.00
- ✅ E5.4 rechazar devuelve el pedido a Borrador y avisa — {"estado":"borrador","aviso_cliente":"enviado"}
- ✅ E5.5 comprobante nuevo correcto: Por confirmar, sin marca de monto — observaciones ['destinatario_no_verificado']
- ✅ E5.6 confirmar cambia el estado y avisa; la segunda confirmación es 409 (5c) — {'estado': 'confirmado', 'aviso_cliente': 'enviado'} / segunda 409
- ✅ E5.7 en CERRADO no pide comprobante ni reenvía métodos — No tengo ese dato confirmado, Ana. ¿Quieres que te derive con una persona para que te indique el tiempo exacto de entrega?
- ✅ E5.8 En curso y Completado funcionan en orden
- ✅ Los pedidos de prueba solo salen con la casilla
- ✅ T1.2 2 polos: subtotal 70.00, descuento 35.00, total 35.00 — 70.00 / 35.00 / 35.00
- ✅ T1.3 no pregunta delivery o recojo (solo delivery); pide zona y dirección — faltan ['direccion', 'zona_envio']
- ✅ T1.4 Miraflores: envío 8.00 y total 43.00 — envío 8.00, total 43.00
- ✅ El agente dice el total del pedido (43.00), no uno propio — Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí. Perfecto, ya te envié los métodos de pago. Tu total es *
- ✅ T1.6 comprobante: Por confirmar con 'El monto no coincide' (la captura dice 500) — ['monto_distinto', 'destinatario_no_verificado']
- ✅ T1.8 confirmar cierra la venta (CERRADO)
- ✅ T1b 2 mochilas (240.00): envío 0.00 — envío 0.00, total 240.00
- ✅ T2.2 tras un rechazo reenvía los métodos una vez — etapa VERIFICACION
- ✅ T2.3 cambiar el pedido saca al cliente de VERIFICACION — etapa COBRO
- ✅ T3.2 145.00 + Callao 12.00 = 157.00 — envío 12.00, total 157.00, modalidad contra_entrega
- ✅ T3.3 en contra entrega no manda cuentas — ¡Gracias, Ana! Ya quedó todo listo: *mochila ejecutiva* x1 y *gorra urbana* x1. Subtotal *S/ 145.00*, envío al *Callao* *S/ 12.00*, total *S/ 157.00*. ¿Te comparto ahora los métodos de pago?
- ✅ T3.4 confirmar deja el pedido Por confirmar — etapa VERIFICACION
- ✅ T3b rechazar contra entrega vuelve a COBRO, sin rechazo vigente — etapa COBRO
