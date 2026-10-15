# Aceptación de la 004 — recorrido automático con el modelo real


## Capacita 1 — orgánica nueva

- **Cliente:** Hola, buenas tardes
  - etapa INICIO → CALIFICAR · embudo NUEVO · tools [enviar_recurso({"clave": "saludo"})→no_disponible]
  - **Leo** (texto): Hola, buenas tardes 😊 ¿Cómo te llamas?
- **Cliente:** Me llamo Ana
  - etapa CALIFICAR → CALIFICAR · embudo NUEVO · tools [mostrar_opciones({"tipo": "caminos", "texto": "Ana, ¿quieres solo certificar la experiencia que ya tienes, o llevar un programa completo?)→hecho]
  - **Leo** (botones): Ana, ¿quieres solo certificar la experiencia que ya tienes, o llevar un programa completo? · botones: Solo certificar, Programa completo
- ✅ Saluda, pide el nombre y con el nombre ofrece los caminos en botones — etapa CALIFICAR, botones ['Solo certificar', 'Programa completo']

## Capacita 2 — calificación respondida escribiendo

- **Cliente:** Hola, soy Ana
  - etapa INICIO → CALIFICAR · embudo NUEVO · tools [enviar_recurso({"clave": "saludo"})→no_disponible]
  - **Leo** (texto): ¡Hola, Ana! 😊 ¿Cómo te llamas completo?
- **Cliente:** Solo quiero certificar mi experiencia en SIAF
  - etapa CALIFICAR → IDENTIFICAR · embudo ATENDIDO · tools [elegir_camino({"camino": "certificacion"})→hecho]
  - **Leo** (texto): Perfecto, Ana. Entonces buscas *solo certificar tu experiencia* en SIAF. ¿Quieres que te comparta las opciones para ese camino?
- ✅ El camino escrito queda registrado y avanza a IDENTIFICAR — camino certificacion, etapa IDENTIFICAR

## Capacita 3 — entrada por anuncio (canal real)

- **Cliente:** Hola, vi su anuncio, quiero más información
  - **Leo:** ¡Hola! 😊 Soy Leo de Escuela Capacita. ¿Cómo te llamas? / Hola, Ana. Te comparto lo que vi para *certificar tu experiencia en SIAF*: la *Certificación individual SIAF* está a *S/ 120.00*.

También hay otras opciones relacionadas por si quieres comparar:
• Contrataciones con el Estado
• Derecho administrativo
• Gestión pública
• Paquetes de certificaciones

Si quieres, te digo cuál te conviene más según lo que buscas.
- ✅ El anuncio queda en los hechos y la respuesta habla de lo que anunciaba — etapa IDENTIFICAR

## Capacita 6 — «lo pensaré» con promoción de cierre

- **Cliente:** Hola, soy Ana. Solo quiero certificar mi experiencia
  - etapa INICIO → CALIFICAR · embudo NUEVO · tools [enviar_recurso({"clave": "saludo"})→no_disponible]
  - **Leo** (texto): ¡Hola, Ana! 😊 Encantado de ayudarte a certificar tu experiencia. ¿Qué experiencia o tema quieres certificar?
- **Cliente:** Quiero la certificación individual de SIAF
  - etapa CALIFICAR → IDENTIFICAR · embudo ATENDIDO · tools [elegir_camino({"camino": "certificacion"})→hecho]
  - **Leo** (texto): Perfecto, Ana. Ya registré que buscas *certificación individual de SIAF*. ¿Quieres que te comparta las opciones disponibles?
- (el agente preguntó antes de agregar; el cliente responde «sí»)
- **Cliente:** Sí
  - etapa IDENTIFICAR → IDENTIFICAR · embudo ATENDIDO · tools [buscar_productos({"consulta": "SIAF", "categoria": "certificacion"})→hecho]
  - **Leo** (texto): La certificación individual de *SIAF* cuesta *S/ 120.00*.

También tienes estas opciones:
• Paquete 2 certificaciones: S/ 200.00
• Paquete 5 certificaciones: S/ 500.00
• Paquete 10 certificaciones: S/ 900.00

Si quieres, te ayudo a elegir la mejor opción.
- **Cliente:** Mmm, está un poco caro. Lo voy a pensar
  - etapa IDENTIFICAR → IDENTIFICAR · embudo ATENDIDO · tools [gestionar_pedido({"accion": "aplicar_promocion", "promocion_id": "fd8f6834-6456-4593-81d4-40b631b8b2d2"})→hecho]
  - **Leo** (texto): Te dejo una promo de cierre: *10 % si te inscribes hoy*; queda disponible por 24 horas y se aplicará cuando armemos tu pedido. ¿Quieres que te muestre las opciones para elegir?
- ✅ Ofrece la promoción de cierre una vez y la registra — promos_ofrecidas {'fd8f6834-6456-4593-81d4-40b631b8b2d2': '2026-10-15T19:00:49.983621Z'}
- **Cliente:** Ok, igual lo pienso
  - etapa IDENTIFICAR → IDENTIFICAR · embudo ATENDIDO · tools []
  - **Leo** (texto): Entiendo, Ana. Si luego quieres retomar tu certificación de SIAF, aquí estoy para ayudarte.
- ✅ No la vuelve a ofrecer ni inventa otro descuento — Entiendo, Ana. Si luego quieres retomar tu certificación de SIAF, aquí estoy para ayudarte.

## Tienda — 3 productos con promoción, delivery, cobro, comprobante y confirmación

- **Cliente:** Hola, quiero 2 polos básicos y una gorra urbana
  - etapa INICIO → DATOS · embudo NEGOCIACION · tools [enviar_recurso({"clave": "saludo"})→no_disponible; buscar_productos({"consulta": "polos básicos", "categoria": "polos"})→hecho; buscar_productos({"consulta": "gorra urbana", "categoria": "accesorios"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "a9fa3f9d-41f2-48c2-91ca-5df2ed91956b", "cantidad": 2})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "8a80ac31-d6ad-4184-bca3-2ebd5c30cccc", "cantidad": 1})→hecho] · pedido borrador 60.00
  - **Leo** (texto): Listo, te quedó: 2 polos básicos y 1 gorra urbana. Total: S/ 60.00. ¿Quieres que te ayude con el envío?
- ✅ Pedido de 3 productos con el 2x1: subtotal 95.00, descuento 35.00, total 60.00 — 3 unidades, 95.00 / 35.00 / 60.00
- **Cliente:** Soy Ana Quispe, celular 987654321
  - etapa DATOS → DATOS · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "telefono": "****4321"})→hecho] · pedido borrador 60.00
  - **Leo** (texto): Gracias, Ana 😊 Para el delivery, llegamos a *Miraflores* y *Callao*. ¿A cuál de esas zonas va tu pedido?
- **Cliente:** Delivery a Miraflores, Av. Larco 123, frente al parque
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"tipo_entrega": "delivery", "direccion": "***", "zona_envio": "Miraflores"})→hecho] · pedido borrador 68.00
  - **Leo** (texto): ¡Gracias! Ya quedó todo listo con tu delivery a Miraflores. ¿Quieres que te comparta los métodos de pago?
- **Cliente:** ¿Cómo pago?
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→hecho] · pedido borrador 68.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo.
  - **Leo** (texto): Tu pedido se paga por *Yape* antes de entregar. El total es *S/ 68.00*. Cuando pagues, envíame por aquí la foto del comprobante, por favor.
- ✅ Delivery a Miraflores: envío 8.00, total 68.00, y le envía los métodos de pago — envío 8.00, total 68.00
- **Cliente:** [imagen comprobante-ok.png]
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [] · pedido por_confirmar 68.00
  - **Leo** (texto): Gracias, Ana. Tu comprobante está en revisión y te avisaremos apenas se confirme.
- **Cliente:** Gracias
  - etapa CERRADO → CERRADO · embudo GANADO · tools [] · pedido confirmado 68.00
  - **Leo** (texto): ¡De nada, Ana! Si luego quieres agregar algo más a tu pedido, dime qué necesitas.
- ✅ El comprobante deja el pedido por confirmar y el operador lo confirma — 200, etapa CERRADO

## Zentek360 — cotización derivada con resumen

- **Cliente:** Hola, necesito una página web para mi restaurante, con carta y reservas
  - etapa INICIO → IDENTIFICAR · embudo ATENDIDO · tools [enviar_recurso({"clave": "saludo"})→no_disponible]
  - **Leo** (texto): Hola, soy Leo de Zentek360 👋

Sí, podemos ayudarte con una página web para tu restaurante con carta y reservas. ¿Quieres que te comparta más info o prefieres que te derive con una persona para revisar tu caso?
- **Cliente:** Para el próximo mes, presupuesto unos 2000 soles
  - etapa IDENTIFICAR → DERIVADO · embudo DERIVADO · tools [buscar_productos({"consulta": "página web para restaurante con carta y reservas", "categoria": "servicios"})→hecho; derivar_a_humano({"motivo": "cotizacion", "resumen": "Cliente necesita una página web para su restaurante con carta y reservas. Quiere ha)→hecho]
  - **Leo** (texto): Te comunico con una persona de nuestro equipo. Te responde en breve.
- ✅ Deriva la cotización con un resumen para el asesor — motivo cotizacion, resumen Cliente necesita una página web para su restaurante con carta y reservas. Quiere hacerlo para el próximo mes y mencionó 

## Zentek360 — hosting de precio fijo vendido

- **Cliente:** Hola, quiero contratar el hosting anual
  - etapa INICIO → IDENTIFICAR · embudo ATENDIDO · tools [enviar_recurso({"clave": "saludo"})→no_disponible; buscar_productos({"consulta": "hosting anual", "categoria": "servicios"})→hecho]
  - **Leo** (texto): Hola, soy Leo de Zentek360 😊

Sí, tenemos *hosting anual* por *S/ 350.00*. ¿Quieres que te ayude a avanzar con el pedido?
- (el agente preguntó antes de agregar; el cliente responde «sí»)
- **Cliente:** Sí
  - etapa IDENTIFICAR → DATOS · embudo NEGOCIACION · tools [buscar_productos({"consulta": "hosting anual", "categoria": "servicios"})→hecho; gestionar_pedido({"accion": "agregar", "producto_id": "bda40c38-19e6-44fd-8fdc-d25f6f70a253", "cantidad": 1})→hecho] · pedido borrador 350.00
  - **Leo** (texto): Listo, ya quedó en tu pedido el *hosting anual* por *S/ 350.00*.

Si quieres, te ayudo con algo más de Zentek360.
- **Cliente:** Soy Carla Díaz, de Restaurante El Sabor, celular 912345678, correo carla@empresa.pe
  - etapa DATOS → COBRO · embudo NEGOCIACION · tools [registrar_datos_cliente({"nombres": "***", "empresa": "***", "correo": "c***@empresa.pe"})→hecho] · pedido borrador 350.00
  - **Leo** (texto): Perfecto, Carla. Ya tengo tus datos y los de tu empresa.

¿Quieres que te comparta los métodos de pago para seguir con tu pedido?
- **Cliente:** ¿Cómo pago?
  - etapa COBRO → COBRO · embudo CUENTAS_ENVIADAS · tools [enviar_metodos_pago({})→hecho] · pedido borrador 350.00
  - **Leo** (texto): Estos son nuestros métodos de pago:

• *Transferencia bancaria*
Transferencia BCP 191-1234567-0-12 a nombre de Zentek360 SAC.
  - **Leo** (texto): Tu pedido total es de *S/ 350.00*.

Cuando pagues, envíame por aquí la foto o captura del comprobante, por favor.
- **Cliente:** [imagen comprobante-ok.png]
  - etapa COBRO → VERIFICACION · embudo EN_VERIFICACION · tools [] · pedido por_confirmar 350.00
  - **Leo** (texto): Gracias, Carla. Tu comprobante está en revisión y te avisaremos apenas se confirme.
- **Cliente:** Gracias
  - etapa CERRADO → CERRADO · embudo GANADO · tools [] · pedido confirmado 350.00
  - **Leo** (texto): ¡De nada, Carla! Si necesitas algo más de Zentek360, aquí estoy.
- ✅ El hosting de S/ 350 llega a cobro, comprobante y confirmación — total 350.00, 200, etapa CERRADO

## En los tres negocios

- ✅ Ningún turno supera 3 mensajes salientes
- ✅ Ningún archivo se envía dos veces en una conversación — []

## Resumen

- ✅ Saluda, pide el nombre y con el nombre ofrece los caminos en botones — etapa CALIFICAR, botones ['Solo certificar', 'Programa completo']
- ✅ El camino escrito queda registrado y avanza a IDENTIFICAR — camino certificacion, etapa IDENTIFICAR
- ✅ El anuncio queda en los hechos y la respuesta habla de lo que anunciaba — etapa IDENTIFICAR
- ✅ Ofrece la promoción de cierre una vez y la registra — promos_ofrecidas {'fd8f6834-6456-4593-81d4-40b631b8b2d2': '2026-10-15T19:00:49.983621Z'}
- ✅ No la vuelve a ofrecer ni inventa otro descuento — Entiendo, Ana. Si luego quieres retomar tu certificación de SIAF, aquí estoy para ayudarte.
- ✅ Pedido de 3 productos con el 2x1: subtotal 95.00, descuento 35.00, total 60.00 — 3 unidades, 95.00 / 35.00 / 60.00
- ✅ Delivery a Miraflores: envío 8.00, total 68.00, y le envía los métodos de pago — envío 8.00, total 68.00
- ✅ El comprobante deja el pedido por confirmar y el operador lo confirma — 200, etapa CERRADO
- ✅ Deriva la cotización con un resumen para el asesor — motivo cotizacion, resumen Cliente necesita una página web para su restaurante con carta y reservas. Quiere hacerlo para el próximo mes y mencionó 
- ✅ El hosting de S/ 350 llega a cobro, comprobante y confirmación — total 350.00, 200, etapa CERRADO
- ✅ Ningún turno supera 3 mensajes salientes
- ✅ Ningún archivo se envía dos veces en una conversación — []
