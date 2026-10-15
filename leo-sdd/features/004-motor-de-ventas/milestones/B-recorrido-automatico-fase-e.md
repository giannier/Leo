# Milestone B — recorrido automático con el modelo real


## Escenario 4 — Capacita, paquete de 5 con 2 temas

- **Cliente:** Hola
  - etapa INICIO → CALIFICAR · embudo NUEVO · tools [enviar_recurso({"clave": "saludo"})→no_disponible]
  - **Leo** (texto): ¡Hola! Soy Leo de Escuela Capacita 😊 ¿Cómo te llamas?
- **Cliente:** Me llamo Ana
  - etapa CALIFICAR → CALIFICAR · embudo NUEVO · tools [mostrar_opciones({"tipo": "caminos", "texto": "Ana, ¿quieres solo certificar la experiencia que ya tienes, o llevar un programa completo?)→hecho]
  - **Leo** (botones): Ana, ¿quieres solo certificar la experiencia que ya tienes, o llevar un programa completo? · botones: Solo certificar, Programa completo
- **Cliente:** [toca «Solo certificar»]
  - etapa CALIFICAR → IDENTIFICAR · embudo ATENDIDO · tools []
  - **Leo** (texto): Perfecto, Ana. ¿Qué tema necesitas certificar?
- **Cliente:** Necesito certificarme en SIAF
  - etapa IDENTIFICAR → IDENTIFICAR · embudo ATENDIDO · tools [buscar_productos({"consulta": "SIAF", "categoria": "certificacion"})→hecho; mostrar_opciones({"tipo": "productos", "filtro": "paquetes", "texto": "Ana, el certificado individual de SIAF cuesta S/ 120.00. Si quiere)→hecho]
  - **Leo** (botones): Ana, el certificado individual de SIAF cuesta S/ 120.00. Si quieres ahorrar, mira estos paquetes y elige el que te convenga. · botones: Paquete 5 certifica…, Paquete 10 certific…, Paquete 2 certifica…
  - **Leo** (texto): El certificado individual de SIAF cuesta S/ 120.00.
- **Cliente:** [toca «Paquete 5 certifica…»]
  - etapa IDENTIFICAR → PEDIDO · embudo NEGOCIACION · tools [gestionar_pedido({"accion": "agregar", "producto_id": "12aba8a3-4ef6-4dd5-b99f-2d07f71a1da7"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Listo, Ana. Ya quedó agregado el *Paquete 5 certificaciones* en tu pedido. Falta que elijas 5 temas más para completarlo. ¿Qué otros temas quieres certificar?
- **Cliente:** Quiero ese, con SIAF y Excel avanzado
  - etapa PEDIDO → PEDIDO · embudo NEGOCIACION · tools [gestionar_pedido({"accion": "elegir", "seleccion": "SIAF, Excel avanzado"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Quedó registrado, Ana: elegiste *SIAF* y *Excel avanzado*. Te faltan *3* temas más para completar el paquete. ¿Qué otros temas quieres certificar?
- ✅ E4.1 paquete de 5 con 2 temas queda en PEDIDO con faltan 3 — faltan 3, etapa PEDIDO
- **Cliente:** SEACE, Power BI y Gestión de museos
  - etapa PEDIDO → DATOS · embudo NEGOCIACION · tools [gestionar_pedido({"accion": "elegir", "seleccion": "SEACE, Power BI, Gestión de museos"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Quedó registrado, Ana: elegiste *SIAF, Excel avanzado, SEACE, Power BI y Gestión de museos*. Tu paquete ya está completo. ¿Quieres que sigamos con tus datos?
- ✅ E4.2 con 5 temas el pedido está completo y pasa a DATOS; el tema libre se acepta — completo True, etapa DATOS, selecciones [['SIAF', 'Excel avanzado', 'SEACE', 'Power BI', 'Gestión de museos']]
- **Cliente:** Mi DNI es 12345678 y me llamo Ana
  - etapa DATOS → DATOS · embudo NEGOCIACION · tools [registrar_datos_cliente({"dni": "****5678", "nombres": "***"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Gracias, Ana. Me faltan tus *apellidos*, *correo* y *teléfono* para registrar tu certificado y enviarte la información de pago. ¿Me los compartes?
- ✅ E4.3 registra lo válido y pide solo lo que falta, sin métodos de pago aún — faltan ['apellidos', 'correo', 'telefono']
- ✅ Datos personales enmascarados en el debug (DNI)
- **Cliente:** Quispe Rojas, ana@example.com, 987654321
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"apellidos": "***", "correo": "a***@example.com", "telefono": "****4321"})→hecho] · pedido borrador 500.00
  - **Leo** (texto): ¡Gracias, Ana! Ya está todo listo con tus datos. Ahora seguimos con el siguiente paso del pedido.
- **Cliente:** Sí, pásame los métodos de pago
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→hecho] · pedido borrador 500.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Yape*
Yape al 987654321 a nombre de Escuela Capacita. Cuando pagues, envía la foto del comprobante por aquí.
  - **Leo** (texto): Perfecto, Ana. Tu total es S/ 500.00. Cuando hagas el pago, envíame por aquí la foto o captura del comprobante.
- ✅ E4.4-5 datos completos sin pedir dirección; envía los métodos de pago — etapa COBRO
- **Cliente:** ¿Cuáles eran las cuentas?
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→omitido] · pedido borrador 500.00
  - **Leo** (texto): Revisa el mensaje anterior, Ana. Cuando pagues, envíame por aquí la foto o captura del comprobante.
- ✅ E4.6 no reenvía los métodos de pago (una vez por pedido)

## Escenario 5 — comprobante y confirmación

- **Cliente:** [imagen no-comprobante.png]
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [] · pedido borrador 500.00
  - **Leo** (texto): ¿Me envías la foto del comprobante de pago, por favor?
- ✅ E5.1 una foto que no es comprobante no crea comprobante ni cambia la etapa — etapa COBRO
- **Cliente:** [imagen comprobante-monto.png]
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [gestionar_pedido({"accion": "ver"})→hecho] · pedido por_confirmar 500.00
  - **Leo** (texto): Gracias, Ana. Tu pedido está completo: *Paquete 5 certificaciones* con SIAF, Excel avanzado, SEACE, Power BI y Gestión de museos. Total: *S/ 500.00*. Tu comprobante está en revisión y te avisaremos apenas se confirme.
- ✅ E5.2 comprobante con otro monto: pedido Por confirmar, marcado y no rechazado — estado por_confirmar, observaciones ['monto_distinto', 'destinatario_no_verificado', 'fecha_anterior_al_pedido']
- ✅ E5.2 el agente no dice que el pago está confirmado — Gracias, Ana. Tu pedido está completo: *Paquete 5 certificaciones* con SIAF, Excel avanzado, SEACE, Power BI y Gestión de museos. Total: *S/ 500.00*. Tu comprob
- ✅ E5.3 detalle con ítems, selecciones, comprobante leído, observaciones e historial — monto leído 450.00
- ✅ E5.4 rechazar devuelve el pedido a Borrador y avisa — {"estado":"borrador","aviso_cliente":"enviado"}
- **Cliente:** [imagen comprobante-ok.png]
  - etapa VERIFICACION → VERIFICACION · embudo EN_VERIFICACION · tools [] · pedido por_confirmar 500.00
  - **Leo** (texto): Ana, tu comprobante sigue en revisión. Apenas se confirme, te avisamos por aquí.
- ✅ E5.5 comprobante nuevo correcto: Por confirmar, sin marca de monto — observaciones ['destinatario_no_verificado', 'fecha_anterior_al_pedido']
- ✅ E5.6 confirmar cambia el estado y avisa; la segunda confirmación es 409 (5c) — {'estado': 'confirmado', 'aviso_cliente': 'enviado'} / segunda 409
- **Cliente:** ¿Cuándo me llegan?
  - etapa CERRADO → CERRADO · embudo GANADO · tools [consultar_conocimiento({"consulta": "tiempo de entrega de certificados después de pago confirmado, cómo los recibe el cliente"})→no_disponible] · pedido confirmado 500.00
  - **Leo** (texto): No tengo ese dato confirmado en la información del negocio. Si quieres, te derivo con una persona para que te indique cuándo te llegan y cómo los recibes.
- ✅ E5.7 en CERRADO no pide comprobante ni reenvía métodos — No tengo ese dato confirmado en la información del negocio. Si quieres, te derivo con una persona para que te indique cuándo te llegan y cómo los recibes.
- ✅ E5.8 En curso y Completado funcionan en orden
- ✅ Los pedidos de prueba solo salen con la casilla

## T1 — dos polos, delivery, comprobante, confirmación

- **Cliente:** Hola
  - etapa INICIO → IDENTIFICAR · embudo ATENDIDO · tools [enviar_recurso({"clave": "saludo"})→no_disponible]
  - **Leo** (texto): ¡Hola! Soy Leo de Tienda Demo 😊 ¿En qué te puedo ayudar?
- **Cliente:** Quiero 2 polos
  - etapa IDENTIFICAR → DATOS · embudo NEGOCIACION · tools [buscar_productos({"consulta": "polos", "categoria": "polos"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "90bcff6e-cd2e-4ed3-ae03-08703ed5166a", "cantidad": 2})→hecho] · pedido borrador 35.00
  - **Leo** (texto): Listo, ya quedó en tu pedido: *2 Polo básico* por *S/ 35.00* c/u. Con la promo 2x1, tu total queda en *S/ 35.00*.

¿Deseas agregar algo más?
- ✅ T1.2 2 polos: subtotal 70.00, descuento 35.00, total 35.00 — 70.00 / 35.00 / 35.00
- **Cliente:** Soy Ana Quispe, mi número es 987654321
  - etapa DATOS → DATOS · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "telefono": "****4321"})→hecho] · pedido borrador 35.00
  - **Leo** (texto): Gracias, Ana. Para completar tu pedido, solo me falta tu *dirección* y tu *zona de envío*.

Llegamos a *Miraflores, San Isidro y Callao*; ¿cuál de esas zonas te sirve?
- ✅ T1.3 no pregunta delivery o recojo (solo delivery); pide zona y dirección — faltan ['direccion', 'zona_envio']
- **Cliente:** Delivery en Miraflores, Av. Larco 123
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"tipo_entrega": "delivery", "direccion": "***", "zona_envio": "Miraflores"})→hecho; gestionar_pedido({"accion": "ver"})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Perfecto, Ana. Tu pedido quedó así: *2 Polo básico* por *S/ 35.00* c/u, *descuento S/ 35.00* por la promo 2x1, *envío S/ 8.00* a Miraflores. *Total: S/ 43.00*.

¿Te comparto los métodos de pago?
- **Cliente:** Sí, pásame cómo pagar
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí.
  - **Leo** (texto): Perfecto, Ana. Tu total es *S/ 43.00*.

Cuando pagues, envíame por aquí la foto del comprobante, por favor.
- ✅ T1.4 Miraflores: envío 8.00 y total 43.00 — envío 8.00, total 43.00
- ✅ El agente dice el total del pedido (43.00), no uno propio — Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí. Perfecto, Ana. Tu total es *S/ 43.00*.

Cuando pagues, e
- **Cliente:** [imagen comprobante-ok.png]
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [] · pedido por_confirmar 43.00
  - **Leo** (texto): Tu comprobante está en revisión. Apenas se confirme, te avisamos.
- ✅ T1.6 comprobante: Por confirmar con 'El monto no coincide' (la captura dice 500) — ['monto_distinto', 'destinatario_no_verificado', 'fecha_anterior_al_pedido']
- **Cliente:** Gracias
  - etapa CERRADO → CERRADO · embudo GANADO · tools [] · pedido confirmado 43.00
  - **Leo** (texto): ¡De nada, Ana! Si luego quieres agregar algo más a tu pedido, dime qué necesitas.
- ✅ T1.8 confirmar cierra la venta (CERRADO)

## T1b — envío gratis

- **Cliente:** Hola, quiero 2 mochilas
  - etapa INICIO → IDENTIFICAR · embudo ATENDIDO · tools [enviar_recurso({"clave": "saludo"})→no_disponible; buscar_productos({"consulta": "mochilas", "categoria": "mochilas"})→hecho]
  - **Leo** (texto): Hola, soy Leo de Tienda Demo 😊

Sí tenemos *Mochila ejecutiva* a S/ 120.00. ¿Quieres 2 unidades de esa mochila?
- (el agente preguntó antes de agregar; el cliente responde «sí»)
- **Cliente:** Sí
  - etapa IDENTIFICAR → DATOS · embudo NEGOCIACION · tools [buscar_productos({"consulta": "mochilas", "categoria": "mochilas"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "68e213d9-9606-4071-95c8-5096930b3eaf", "cantidad": 2})→hecho] · pedido borrador 240.00
  - **Leo** (texto): Listo, ya quedó en tu pedido: *2 Mochila ejecutiva* por *S/ 240.00* en total. ¿Quieres agregar algo más?
- **Cliente:** Ana Quispe, 987654321, Miraflores, Av. Larco 123
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "telefono": "****4321", "zona_envio": "Miraflores", "direccion": "***"})→hecho] · pedido borrador 240.00
  - **Leo** (texto): Ya están todos los datos. ¿Quieres que siga con el siguiente paso del pedido?
- ✅ T1b 2 mochilas (240.00): envío 0.00 — envío 0.00, total 240.00

## T2 — rechazo y corrección

- **Cliente:** Hola, quiero 2 polos
  - etapa INICIO → IDENTIFICAR · embudo ATENDIDO · tools [enviar_recurso({"clave": "saludo"})→no_disponible; buscar_productos({"consulta": "polos", "categoria": "polos"})→hecho]
  - **Leo** (texto): Hola, soy Leo de Tienda Demo 😊

Sí tenemos *Polo básico* a S/ 35.00 y aplica la promo *2x1 en polos*: llevas 2 y pagas 1.

¿Quieres que te agregue 2 polos al pedido?
- (el agente preguntó antes de agregar; el cliente responde «sí»)
- **Cliente:** Sí
  - etapa IDENTIFICAR → DATOS · embudo NEGOCIACION · tools [buscar_productos({"consulta": "polo", "categoria": "polos"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "90bcff6e-cd2e-4ed3-ae03-08703ed5166a", "cantidad": 2})→hecho] · pedido borrador 35.00
  - **Leo** (texto): Listo, ya quedó agregado el *Polo básico x2* al pedido.

Subtotal: S/ 70.00
Descuento: S/ 35.00
Total: S/ 35.00

¿Quieres agregar algo más?
- **Cliente:** Soy Ana Quispe, 987654321, delivery en Miraflores, Av. Larco 123
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "telefono": "****4321", "tipo_entrega": "delivery", "direccion": "***", "zona_envio": "Miraflores"})→hecho; gestionar_pedido({"accion": "ver"})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Gracias, Ana. Ya quedó todo registrado.

Tu pedido quedó así:
*2 polos básicos*
Subtotal: S/ 70.00
Descuento: S/ 35.00
Envío a Miraflores: S/ 8.00
Total: S/ 43.00

¿Quieres que te comparta los métodos de pago?
- **Cliente:** Sí, pásame cómo pagar
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí.
  - **Leo** (texto): Perfecto, Ana. Tu total es *S/ 43.00*.

Cuando hagas el pago, envíame por aquí la foto o captura del comprobante, por favor.
- **Cliente:** [imagen comprobante-ok.png]
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [] · pedido por_confirmar 43.00
  - **Leo** (texto): Gracias, Ana. Tu comprobante ya está en revisión y te avisaremos apenas se confirme.

¿Quieres agregar o cambiar algo de tu pedido?
- **Cliente:** Pásame de nuevo las cuentas
  - etapa VERIFICACION → VERIFICACION · embudo EN_VERIFICACION · tools [enviar_metodos_pago({})→hecho] · pedido borrador 43.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí.
  - **Leo** (texto): Listo, Ana. Tu total es *S/ 43.00*.

Cuando hagas el pago, envíame por aquí la foto o captura del comprobante, por favor.
- ✅ T2.2 tras un rechazo reenvía los métodos una vez — etapa VERIFICACION
- **Cliente:** Mejor agrégame una gorra
  - etapa VERIFICACION → COBRO · embudo CUENTAS_ENVIADAS · tools [buscar_productos({"consulta": "gorra", "categoria": "productos"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "94611af4-8be3-4526-a7ce-9eaa2cdbc3ee", "cantidad": 1})→hecho] · pedido borrador 68.00
  - **Leo** (texto): Listo, Ana. Ya te agregué la *Gorra urbana* al pedido.

Quedó así:
*2 polos básicos + 1 gorra urbana*
Subtotal: S/ 95.00
Descuento: S/ 35.00
Envío: S/ 8.00
Total: S/ 68.00

¿Quieres agregar algo más?
- ✅ T2.3 cambiar el pedido saca al cliente de VERIFICACION — etapa COBRO

## T3 — contra entrega

- **Cliente:** Hola, quiero una mochila y una gorra
  - etapa INICIO → IDENTIFICAR · embudo ATENDIDO · tools [enviar_recurso({"clave": "saludo"})→no_disponible; buscar_productos({"consulta": "mochila", "categoria": "productos"})→hecho; buscar_productos({"consulta": "gorra", "categoria": "productos"})→hecho]
  - **Leo** (texto): Hola, soy Leo de Tienda Demo. Tengo *Mochila ejecutiva* a S/ 120.00 y *Gorra urbana* a S/ 25.00. ¿Quieres que te las agregue al pedido?
- (el agente preguntó antes de agregar; el cliente responde «sí»)
- **Cliente:** Sí
  - etapa IDENTIFICAR → DATOS · embudo NEGOCIACION · tools [buscar_productos({"consulta": "mochila", "categoria": "mochilas"})→hecho; buscar_productos({"consulta": "gorra", "categoria": "gorras"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "68e213d9-9606-4071-95c8-5096930b3eaf", "cantidad": 1})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "94611af4-8be3-4526-a7ce-9eaa2cdbc3ee", "cantidad": 1})→hecho] · pedido borrador 145.00
  - **Leo** (texto): Listo, ya quedaron en tu pedido la *mochila ejecutiva* y la *gorra urbana*.

Subtotal: S/ 145.00
Descuento: S/ 0.00
Total: S/ 145.00

¿Deseas algo más?
- **Cliente:** Ana Quispe, 987654321, delivery al Callao, Jr. Unión 45
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "telefono": "****4321", "tipo_entrega": "delivery", "direccion": "***", "zona_envio": "Callao"})→hecho; gestionar_pedido({"accion": "ver"})→hecho] · pedido borrador 157.00
  - **Leo** (texto): Disculpa, no quiero darte el nombre equivocado de un producto. Déjame comunicarte con una persona del negocio para confirmártelo bien.
- ✅ T3.2 145.00 + Callao 12.00 = 157.00 — envío 12.00, total 157.00, modalidad contra_entrega
- ✅ T3.3 en contra entrega no manda cuentas — Disculpa, no quiero darte el nombre equivocado de un producto. Déjame comunicarte con una persona del negocio para confirmártelo bien.
- **Cliente:** Sí, confirmo
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [gestionar_pedido({"accion": "confirmar"})→hecho] · pedido por_confirmar 157.00
  - **Leo** (texto): ¡Gracias! Tu pedido quedó enviado al negocio para revisión. Total: S/ 157.00 con delivery al Callao. Te avisarán cuando lo confirmen.
- ✅ T3.4 confirmar deja el pedido Por confirmar — etapa VERIFICACION
- **Cliente:** Ok, entonces qué hago
  - etapa COBRO → DERIVADO · embudo DERIVADO · tools [consultar_conocimiento({"consulta": "Qué opciones de entrega o qué puedo hacer si no llegamos a la zona Callao esta semana"})→no_disponible; derivar_a_humano({"motivo": "otro", "resumen": "La clienta tiene pedido #4 con delivery al Callao, pero el negocio indicó que no llegan a)→hecho] · pedido borrador 157.00
  - **Leo** (texto): Te comunico con una persona de nuestro equipo. Te responde en breve.
- ❌ T3b rechazar contra entrega vuelve a COBRO, sin rechazo vigente — etapa DERIVADO

## Resumen del checklist

- ✅ E4.1 paquete de 5 con 2 temas queda en PEDIDO con faltan 3 — faltan 3, etapa PEDIDO
- ✅ E4.2 con 5 temas el pedido está completo y pasa a DATOS; el tema libre se acepta — completo True, etapa DATOS, selecciones [['SIAF', 'Excel avanzado', 'SEACE', 'Power BI', 'Gestión de museos']]
- ✅ E4.3 registra lo válido y pide solo lo que falta, sin métodos de pago aún — faltan ['apellidos', 'correo', 'telefono']
- ✅ Datos personales enmascarados en el debug (DNI)
- ✅ E4.4-5 datos completos sin pedir dirección; envía los métodos de pago — etapa COBRO
- ✅ E4.6 no reenvía los métodos de pago (una vez por pedido)
- ✅ E5.1 una foto que no es comprobante no crea comprobante ni cambia la etapa — etapa COBRO
- ✅ E5.2 comprobante con otro monto: pedido Por confirmar, marcado y no rechazado — estado por_confirmar, observaciones ['monto_distinto', 'destinatario_no_verificado', 'fecha_anterior_al_pedido']
- ✅ E5.2 el agente no dice que el pago está confirmado — Gracias, Ana. Tu pedido está completo: *Paquete 5 certificaciones* con SIAF, Excel avanzado, SEACE, Power BI y Gestión de museos. Total: *S/ 500.00*. Tu comprob
- ✅ E5.3 detalle con ítems, selecciones, comprobante leído, observaciones e historial — monto leído 450.00
- ✅ E5.4 rechazar devuelve el pedido a Borrador y avisa — {"estado":"borrador","aviso_cliente":"enviado"}
- ✅ E5.5 comprobante nuevo correcto: Por confirmar, sin marca de monto — observaciones ['destinatario_no_verificado', 'fecha_anterior_al_pedido']
- ✅ E5.6 confirmar cambia el estado y avisa; la segunda confirmación es 409 (5c) — {'estado': 'confirmado', 'aviso_cliente': 'enviado'} / segunda 409
- ✅ E5.7 en CERRADO no pide comprobante ni reenvía métodos — No tengo ese dato confirmado en la información del negocio. Si quieres, te derivo con una persona para que te indique cuándo te llegan y cómo los recibes.
- ✅ E5.8 En curso y Completado funcionan en orden
- ✅ Los pedidos de prueba solo salen con la casilla
- ✅ T1.2 2 polos: subtotal 70.00, descuento 35.00, total 35.00 — 70.00 / 35.00 / 35.00
- ✅ T1.3 no pregunta delivery o recojo (solo delivery); pide zona y dirección — faltan ['direccion', 'zona_envio']
- ✅ T1.4 Miraflores: envío 8.00 y total 43.00 — envío 8.00, total 43.00
- ✅ El agente dice el total del pedido (43.00), no uno propio — Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo. Cuando pagues, envía la foto del comprobante por aquí. Perfecto, Ana. Tu total es *S/ 43.00*.

Cuando pagues, e
- ✅ T1.6 comprobante: Por confirmar con 'El monto no coincide' (la captura dice 500) — ['monto_distinto', 'destinatario_no_verificado', 'fecha_anterior_al_pedido']
- ✅ T1.8 confirmar cierra la venta (CERRADO)
- ✅ T1b 2 mochilas (240.00): envío 0.00 — envío 0.00, total 240.00
- ✅ T2.2 tras un rechazo reenvía los métodos una vez — etapa VERIFICACION
- ✅ T2.3 cambiar el pedido saca al cliente de VERIFICACION — etapa COBRO
- ✅ T3.2 145.00 + Callao 12.00 = 157.00 — envío 12.00, total 157.00, modalidad contra_entrega
- ✅ T3.3 en contra entrega no manda cuentas — Disculpa, no quiero darte el nombre equivocado de un producto. Déjame comunicarte con una persona del negocio para confirmártelo bien.
- ✅ T3.4 confirmar deja el pedido Por confirmar — etapa VERIFICACION
- ❌ T3b rechazar contra entrega vuelve a COBRO, sin rechazo vigente — etapa DERIVADO
