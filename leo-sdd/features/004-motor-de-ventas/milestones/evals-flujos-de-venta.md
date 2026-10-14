# Evals de flujos de venta (TASK-447)

## productos: 8/8 (100%)

### ✅ pregunta un precio (final IDENTIFICAR)
- Cliente: Hola
  - INICIO → IDENTIFICAR · tools ['enviar_recurso']
  - Leo: Hola, soy Leo de Tienda Demo 😊 ¿En qué te puedo ayudar?
- Cliente: ¿Cuánto cuesta la mochila ejecutiva?
  - IDENTIFICAR → IDENTIFICAR · tools ['buscar_productos']
  - Leo: La *mochila ejecutiva* cuesta *S/ 120.00*. ¿Quieres que te la agregue al pedido?

### ✅ agrega una gorra (final DATOS)
- Cliente: Hola, quiero una gorra urbana
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Tienda Demo 😊

Sí tenemos *Gorra urbana* a S/ 25.00. ¿Quieres que te la agregue al pedido?
- Cliente: Sí, agrégala. Solo una
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, quedó *1 Gorra urbana* en tu pedido por *S/ 25.00*. ¿Quieres algo más?

### ✅ pedido completo hasta el cobro (final COBRO)
- Cliente: Hola, quiero la mochila ejecutiva
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Tienda Demo 😊

Sí, tengo la *Mochila ejecutiva* a S/ 120.00. ¿Quieres que te la agregue al pedido?
- Cliente: Sí, agrégala
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, quedó en tu pedido: *Mochila ejecutiva x1* por *S/ 120.00*. Total: *S/ 120.00*. ¿Deseas algo más?
- Cliente: Me llamo Ana Torres, mi celular es 987654321. Delivery a Miraflores, Av. Larco 123, referencia frente al parque.
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: ¡Gracias, Ana! Ya quedó todo listo con tus datos. ¿Quieres que te comparta los métodos de pago?

### ✅ pregunta por promociones (final IDENTIFICAR)
- Cliente: Hola, ¿tienen alguna promoción?
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'mostrar_opciones']
  - Leo: Sí, tenemos una promoción vigente en Tienda Demo:
  - Leo: Hola, soy Leo de Tienda Demo 😊 ¿Quieres ver la promoción vigente?

### ✅ pide una persona (final DERIVADO)
- Cliente: Hola
  - INICIO → IDENTIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola! Soy Leo de Tienda Demo 😊 ¿En qué te puedo ayudar?
- Cliente: Quiero hablar con una persona, por favor
  - IDENTIFICAR → DERIVADO · tools ['derivar_a_humano']
  - Leo: Te comunico con una persona de nuestro equipo. Te responde en breve.

### ✅ producto que no existe (final IDENTIFICAR)
- Cliente: Hola, ¿venden zapatillas?
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Tienda Demo 😊

No veo zapatillas en el catálogo, pero sí tengo polos, gorras y mochilas. ¿Te muestro esas opciones?

### ✅ zona sin delivery (final DATOS)
- Cliente: Quiero un polo básico
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Tienda Demo 👋

Sí, tengo *Polo básico* a S/ 35.00. Además, tiene la promo *2x1 en polos*: llevas 2 y pagas 1.

¿Quieres que te agregue 1 o 2?
- Cliente: Sí, uno
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, te agregué *Polo básico x1* por *S/ 35.00*.

¿Quieres algo más?
- Cliente: Mándalo a Arequipa, a nombre de Ana Torres, celular 987654321
  - DATOS → DATOS · tools ['registrar_datos_cliente', 'gestionar_pedido']
  - Leo: Gracias, Ana. Solo hacemos delivery a *Miraflores, San Isidro y Callao*.

¿A cuál de esas zonas va el envío?

### ✅ pide cómo pagar (final COBRO)
- Cliente: Quiero una gorra urbana
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: ¡Hola! Soy Leo de Tienda Demo 😊

Sí tenemos *Gorra urbana* a S/ 25.00. ¿Quieres que te la agregue al pedido?
- Cliente: Sí, agrégala
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, ya quedó agregada la *gorra urbana* a tu pedido. Total: *S/ 25.00*.

¿Quieres algo más?
- Cliente: Me llamo Ana Torres, mi celular es 987654321. Delivery a Miraflores, Av. Larco 123, referencia frente al parque.
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: Ya están todos los datos. Sigue con el siguiente paso del pedido.
- Cliente: ¿Cómo pago?
  - COBRO → COBRO · tools ['enviar_metodos_pago']
  - Leo: Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo.
  - Leo: El total de tu pedido es *S/ 33.00*. Cuando pagues, envíame por aquí la foto del comprobante, por favor.

## certificaciones: 8/8 (100%)

### ✅ saluda y le ofrecen caminos (final CALIFICAR)
- Cliente: Hola, buenas tardes
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: Hola, buenas tardes 😊 ¿Cómo te llamas?

### ✅ certifica SIAF y pregunta el precio (final IDENTIFICAR)
- Cliente: Hola
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola! Soy Leo de Escuela Capacita 😊 ¿Cómo te llamas?
- Cliente: Soy Luis, solo quiero certificar mi experiencia en SIAF
  - CALIFICAR → IDENTIFICAR · tools ['elegir_camino', 'mostrar_opciones']
  - Leo: Luis, gracias. ¿Quieres solo certificar la experiencia que ya tienes, o llevar un programa completo?
- Cliente: ¿Cuánto cuesta?
  - IDENTIFICAR → IDENTIFICAR · tools ['buscar_productos']
  - Leo: La certificación individual de *SIAF* cuesta *S/ 120.00*.

Si quieres, también te muestro los paquetes para que veas cuál te conviene más.

### ✅ paquete de 5 con 2 temas (final PEDIDO)
- Cliente: Hola, soy Luis. Solo quiero certificar mi experiencia
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola, Luis! 😊 Encantado de ayudarte con tu certificación de experiencia en Escuela Capacita.

Para orientarte mejor, ¿qué experiencia quieres certificar?
- Cliente: Quiero el paquete de 5 certificaciones
  - CALIFICAR → IDENTIFICAR · tools ['elegir_camino']
  - Leo: Perfecto, Luis. Quedó registrado que quieres *solo certificar* tu experiencia.

Para seguir, ¿me dices qué experiencia o tema quieres certificar?
- Cliente: Por ahora SIAF y Excel avanzado
  - IDENTIFICAR → PEDIDO · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, Luis. Ya quedó en tu pedido el *paquete de 5 certificaciones* con *SIAF* y *Excel avanzado*.

Subtotal: *S/ 500.00*
Descuento: *S/ 0.00*
Total: *S/ 500.00*

Te faltan *3 certificaciones* por elegir. ¿Qué otros temas quieres incluir?

### ✅ programa completo se deriva (final DERIVADO)
- Cliente: Hola
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola! Soy Leo de Escuela Capacita 😊 ¿Cómo te llamas?
- Cliente: Quiero llevar un programa completo
  - CALIFICAR → ASESORIA · tools ['elegir_camino']
  - Leo: Perfecto, ya quedó registrado que quieres llevar un *programa completo*. ¿Me dices tu nombre, por favor?
- Cliente: Me interesa Gestión pública, puedo en las noches
  - ASESORIA → DERIVADO · tools ['enviar_recurso', 'derivar_a_humano']
  - Leo: Te comunico con una persona de nuestro equipo. Te responde en breve.

### ✅ lo pensaré (final IDENTIFICAR)
- Cliente: Hola, soy Luis. Solo quiero certificar
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola, Luis! 😊 Encantado de saludarte. ¿Qué experiencia o tema quieres certificar?
- Cliente: ¿Cuánto cuesta la de Power BI?
  - CALIFICAR → IDENTIFICAR · tools ['elegir_camino', 'consultar_conocimiento']
  - Leo: No tengo el precio de la certificación de Power BI en la información disponible. Si quieres, te derivo con una persona para que te lo confirme.
- Cliente: Lo voy a pensar
  - IDENTIFICAR → IDENTIFICAR · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Puedo ayudarte con una opción para cerrar tu certificación de Power BI. ¿Quieres que te la comparta?

### ✅ individual hasta el cobro (final COBRO)
- Cliente: Hola, soy Luis. Solo quiero certificar
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola, Luis! 😊 Encantado de saludarte. ¿Qué experiencia o tema quieres certificar?
- Cliente: Quiero la certificación de Power BI
  - CALIFICAR → IDENTIFICAR · tools ['elegir_camino', 'mostrar_opciones']
  - Leo: Luis, ¿quieres solo certificar la experiencia que ya tienes, o llevar un programa completo?
- Cliente: Sí, agrégala
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, Luis. Ya quedó agregada la *Certificación individual Power BI* por *S/ 120.00*.
Tu total por ahora es *S/ 120.00*. ¿Quieres que te muestre los paquetes disponibles?
- Cliente: Me llamo Luis Pérez Rojas, DNI 45678912, correo luis.perez@mail.com, celular 976543210
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: ¡Gracias, Luis Pérez Rojas! Ya tengo tus datos completos. ¿Quieres que te comparta ahora el método de pago?

### ✅ pregunta algo que el negocio no dijo (final CALIFICAR)
- Cliente: Hola, soy Luis. Solo quiero certificar
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola, Luis! 😊 Encantado de saludarte. ¿Me dices tu nombre completo, por favor?
- Cliente: ¿Las certificaciones están reconocidas por SERVIR?
  - CALIFICAR → CALIFICAR · tools ['consultar_conocimiento']
  - Leo: No tengo esa información en los datos de Escuela Capacita. Si quieres, puedo derivarte con una persona para que te lo confirme.

### ✅ pide los métodos de pago (final COBRO)
- Cliente: Hola, soy Luis. Solo quiero certificar
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola! Soy de Escuela Capacita 😊 ¿Cómo te llamas?
- Cliente: Quiero la certificación individual de SIAF
  - CALIFICAR → IDENTIFICAR · tools ['elegir_camino']
  - Leo: Perfecto, Luis. ¿Quieres que te comparta las opciones de certificación individual de SIAF?
- Cliente: Sí, agrégala
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, Luis. Ya quedó agregada la *Certificación individual SIAF* en tu pedido: 1 unidad a S/ 120.00, total S/ 120.00. ¿Deseas agregar otra certificación?
- Cliente: Me llamo Luis Pérez Rojas, DNI 45678912, correo luis.perez@mail.com, celular 976543210
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: ¡Gracias, Luis! Ya están todos tus datos. ¿Deseas que te comparta ahora los métodos de pago?
- Cliente: ¿A dónde pago?
  - COBRO → COBRO · tools ['enviar_metodos_pago']
  - Leo: Estos son nuestros métodos de pago:

• *Yape*
Yape al 987654321 a nombre de Escuela Capacita.
  - Leo: Ya te envié los métodos de pago. Tu total es S/ 120.00 y, cuando pagues, envíame por aquí la foto del comprobante.

## servicios: 8/8 (100%)

### ✅ pregunta qué hacen (final IDENTIFICAR)
- Cliente: Hola, ¿qué servicios tienen?
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 👋

Tenemos estos servicios:
• *Hosting anual* desde S/ 350.00
• *Desarrollo de página web* se cotiza con una persona, no tiene precio fijo
• *Mantenimiento web mensual* S/ 150.00

Si quieres, te cuento más de uno en específico.

### ✅ una web se cotiza con una persona (final DERIVADO)
- Cliente: Hola
  - INICIO → IDENTIFICAR · tools ['enviar_recurso']
  - Leo: Hola, soy Leo de Zentek360 😊 ¿En qué te puedo ayudar?
- Cliente: Quiero una página web para mi restaurante, con carta y reservas
  - IDENTIFICAR → DERIVADO · tools ['buscar_productos', 'derivar_a_humano']
  - Leo: Te comunico con una persona de nuestro equipo. Te responde en breve.

### ✅ hosting de precio fijo (final DATOS)
- Cliente: Hola, quiero contratar el hosting anual
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 😊

Sí, tenemos *Hosting anual* por *S/ 350.00*.

¿Quieres que te comparta más detalles o prefieres que avancemos con tu pedido?
- Cliente: Sí, agrégalo
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, quedó agregado *Hosting anual* x1 en tu pedido.

Subtotal: S/ 350.00
Descuento: S/ 0.00
Total: S/ 350.00

¿Quieres que te ayude con algo más de tu hosting?

### ✅ hosting hasta el cobro (final COBRO)
- Cliente: Hola, quiero el hosting anual
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 😊

Tengo el *Hosting anual* en *S/ 350.00*.

¿Quieres que te comparta más detalles o prefieres avanzar con el pedido?
- Cliente: Sí, agrégalo
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, ya quedó agregado el *Hosting anual* en tu pedido.

*1 x S/ 350.00*
*Total: S/ 350.00*

¿Quieres que te ayude con algo más?
- Cliente: Soy Carla Díaz, RUC 20123456789, correo carla@empresa.pe, celular 912345678
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: Ya están todos los datos. Seguimos con el siguiente paso del pedido.

### ✅ pide un asesor (final DERIVADO)
- Cliente: Hola
  - INICIO → IDENTIFICAR · tools ['enviar_recurso']
  - Leo: Hola, soy Leo de Zentek360 😊 ¿En qué te puedo ayudar?
- Cliente: Prefiero hablar con un asesor
  - IDENTIFICAR → DERIVADO · tools ['derivar_a_humano']
  - Leo: Te comunico con una persona de nuestro equipo. Te responde en breve.

### ✅ precio de una web no se inventa (final IDENTIFICAR)
- Cliente: Hola, ¿cuánto cuesta una página web?
  - INICIO → IDENTIFICAR · tools ['buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 👋

El _desarrollo de página web_ se cotiza con una persona y no tiene precio fijo en el chat. Si quieres, te derivo para que te hagan una cotización según lo que necesitas.

### ✅ precio del mantenimiento (final IDENTIFICAR)
- Cliente: Hola, ¿cuánto cuesta el mantenimiento mensual?
  - INICIO → IDENTIFICAR · tools ['buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 👋

El *mantenimiento web mensual* cuesta *S/ 150.00*.

Si quieres, también te puedo contar qué incluye.

### ✅ pide cómo pagar el hosting (final COBRO)
- Cliente: Hola, quiero el hosting anual
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 😊

Tengo el *Hosting anual* en *S/ 350.00*.

¿Quieres que te comparta más detalles o prefieres avanzar con tu pedido?
- Cliente: Sí, agrégalo
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, quedó agregado el *Hosting anual* en tu pedido.

*Subtotal:* S/ 350.00
*Descuento:* S/ 0.00
*Total:* S/ 350.00

¿Quieres que te comparta los métodos de pago?
- Cliente: Soy Carla Díaz, RUC 20123456789, correo carla@empresa.pe, celular 912345678
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: Gracias, Carla. Ya quedaron tus datos completos.

¿Quieres que te comparta los métodos de pago?
- Cliente: ¿Cómo pago?
  - COBRO → COBRO · tools ['enviar_metodos_pago']
  - Leo: Estos son nuestros métodos de pago:

• *Transferencia bancaria*
Transferencia BCP 191-1234567-0-12 a nombre de Zentek360 SAC.
  - Leo: El total de tu pedido es *S/ 350.00*.

Cuando hagas el pago, envíame por aquí la foto o captura del comprobante, por favor.
