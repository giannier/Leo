<!-- C-25 (2026-10-15): corrida completa con el modelo real tras guardar el paquete nombrado. Resumen de las cinco corridas en clarifications.md, C-25. -->

# Evals de flujos de venta (TASK-447)

## productos: 8/8 (100%)

### ✅ pregunta un precio (final IDENTIFICAR)
- Cliente: Hola
  - INICIO → IDENTIFICAR · tools ['enviar_recurso']
  - Leo: Hola, soy Leo de Tienda Demo 😊 ¿En qué te puedo ayudar?
- Cliente: ¿Cuánto cuesta la mochila ejecutiva?
  - IDENTIFICAR → IDENTIFICAR · tools ['buscar_productos']
  - Leo: La *mochila ejecutiva* cuesta *S/ 120.00* y sí está disponible. ¿Quieres que te la agregue al pedido?

### ✅ agrega una gorra (final DATOS)
- Cliente: Hola, quiero una gorra urbana
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: ¡Hola! Soy Leo de Tienda Demo 😊

Sí tenemos *gorra urbana* a S/ 25.00. ¿Quieres que te la agregue al pedido?
- Cliente: Sí, agrégala. Solo una
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, ya quedó agregada 1 gorra urbana a tu pedido. Total: S/ 25.00. ¿Deseas algo más?

### ✅ pedido completo hasta el cobro (final COBRO)
- Cliente: Hola, quiero la mochila ejecutiva
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Tienda Demo 😊

Sí, tengo la *Mochila ejecutiva* a S/ 120.00. ¿Quieres que te la agregue al pedido?
- Cliente: Sí, agrégala
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, ya quedó en tu pedido la *Mochila ejecutiva* por S/ 120.00. ¿Quieres algo más?
- Cliente: Me llamo Ana Torres, mi celular es 987654321. Delivery a Miraflores, Av. Larco 123, referencia frente al parque.
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: Ya están todos los datos, Ana. Gracias 🙌

### ✅ pregunta por promociones (final IDENTIFICAR)
- Cliente: Hola, ¿tienen alguna promoción?
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'mostrar_opciones']
  - Leo: Sí, tenemos una promoción vigente en Tienda Demo 🙂

### ✅ pide una persona (final DERIVADO)
- Cliente: Hola
  - INICIO → IDENTIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola! Soy Leo de Tienda Demo 😊 ¿En qué te puedo ayudar?
- Cliente: Quiero hablar con una persona, por favor
  - IDENTIFICAR → DERIVADO · tools ['derivar_a_humano']
  - Leo: Te comunico con una persona de nuestro equipo. Te responde en breve.

### ✅ producto que no existe (final IDENTIFICAR)
- Cliente: Hola, ¿venden zapatillas?
  - INICIO → IDENTIFICAR · tools ['buscar_productos']
  - Leo: Hola, soy Leo de Tienda Demo 👋

No veo zapatillas en el catálogo. Sí tenemos polos, gorras y mochilas. ¿Quieres que te muestre esos productos?

### ✅ zona sin delivery (final DATOS)
- Cliente: Quiero un polo básico
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Tienda Demo 👋

Sí tenemos *Polo básico* a *S/ 35.00*. Además, tiene promo *2x1 en polos*: llevas 2 y pagas 1.

¿Quieres que te lo agregue al pedido?
- Cliente: Sí, uno
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, ya quedó en tu pedido el *Polo básico* x1 por *S/ 35.00*.

¿Quieres agregar algo más?
- Cliente: Mándalo a Arequipa, a nombre de Ana Torres, celular 987654321
  - DATOS → DATOS · tools ['registrar_datos_cliente', 'gestionar_pedido']
  - Leo: Gracias, Ana. Solo me falta la *zona de envío*: llegamos a *Miraflores, San Isidro y Callao*.

¿A cuál de esas zonas va?

### ✅ pide cómo pagar (final COBRO)
- Cliente: Quiero una gorra urbana
  - INICIO → IDENTIFICAR · tools ['buscar_productos']
  - Leo: Hola, soy Leo de Tienda Demo 👋

Sí tenemos *gorra urbana* a S/ 25.00. ¿Quieres que la agregue a tu pedido?
- Cliente: Sí, agrégala
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, ya quedó en tu pedido. ¿Deseas algo más?
- Cliente: Me llamo Ana Torres, mi celular es 987654321. Delivery a Miraflores, Av. Larco 123, referencia frente al parque.
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: Ya están todos los datos, gracias. ¿Quieres que siga con el siguiente paso del pedido?
- Cliente: ¿Cómo pago?
  - COBRO → COBRO · tools ['enviar_metodos_pago']
  - Leo: Estos son nuestros métodos de pago:

• *Yape*
Yape al 912345678 a nombre de Tienda Demo.
  - Leo: El total de tu pedido es S/ 33.00. Cuando pagues, envíame por aquí la foto del comprobante, por favor.

## certificaciones: 8/8 (100%)

### ✅ saluda y le ofrecen caminos (final CALIFICAR)
- Cliente: Hola, buenas tardes
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: Hola, buenas tardes 😊 ¿Cómo te llamas?

### ✅ certifica SIAF y pregunta el precio (final IDENTIFICAR)
- Cliente: Hola
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola! 😊 Soy Leo de Escuela Capacita. ¿Cómo te llamas?
- Cliente: Soy Luis, solo quiero certificar mi experiencia en SIAF
  - CALIFICAR → IDENTIFICAR · tools ['registrar_datos_cliente', 'elegir_camino']
  - Leo: Perfecto, Luis. ¿Me compartes tu DNI?
- Cliente: ¿Cuánto cuesta?
  - IDENTIFICAR → IDENTIFICAR · tools ['buscar_productos']
  - Leo: La certificación individual de _SIAF_ cuesta *S/ 120.00*.

Si quieres, también te muestro los paquetes para que compares cuál te conviene más.

### ✅ paquete de 5 con 2 temas (final PEDIDO)
- Cliente: Hola, soy Luis. Solo quiero certificar mi experiencia
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola, Luis! 😊 Encantado de saludarte. En Escuela Capacita te ayudamos a certificar tu experiencia para el sector público. ¿Qué experiencia quieres certificar?
- Cliente: Quiero el paquete de 5 certificaciones
  - CALIFICAR → PEDIDO · tools ['buscar_productos', 'enviar_recurso', 'gestionar_pedido']
  - Leo: Listo, Luis. Ya quedó agregado el *Paquete 5 certificaciones* por *S/ 500.00*. Te faltan *5 temas* por elegir para completar tu pedido. ¿Qué certificaciones quieres incluir?
- Cliente: Por ahora SIAF y Excel avanzado
  - PEDIDO → PEDIDO · tools ['gestionar_pedido']
  - Leo: Quedó registrado: *SIAF* y *Excel avanzado*. Te faltan *3 certificaciones* por elegir para completar el paquete. ¿Qué otros temas quieres incluir?

### ✅ programa completo se deriva (final DERIVADO)
- Cliente: Hola
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola! Soy Leo de Escuela Capacita 😊 ¿Cómo te llamas?
- Cliente: Quiero llevar un programa completo
  - CALIFICAR → ASESORIA · tools ['elegir_camino']
  - Leo: Perfecto, seguimos con *Programa completo*. ¿Cómo te llamas?
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
  - Leo: La certificación individual de Power BI cuesta S/ 120.00.

Si quieres, también puedo mostrarte los paquetes disponibles para que veas cuál te conviene más.

### ✅ individual hasta el cobro (final COBRO)
- Cliente: Hola, soy Luis. Solo quiero certificar
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola, Luis! 😊 Encantado de ayudarte con tu certificación. ¿Qué experiencia o tema quieres certificar?
- Cliente: Quiero la certificación de Power BI
  - CALIFICAR → IDENTIFICAR · tools ['elegir_camino', 'mostrar_opciones']
  - Leo: Luis, ¿quieres solo certificar la experiencia que ya tienes, o llevar un programa completo?
- Cliente: Sí, agrégala
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, Luis. Ya quedó agregada *Certificación individual Power BI* a tu pedido por *S/ 120.00*. ¿Quieres que te muestre los paquetes disponibles?
- Cliente: Me llamo Luis Pérez Rojas, DNI 45678912, correo luis.perez@mail.com, celular 976543210
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: ¡Perfecto, Luis! Ya están todos tus datos. ¿Deseas que te comparta ahora el método de pago?

### ✅ pregunta algo que el negocio no dijo (final CALIFICAR)
- Cliente: Hola, soy Luis. Solo quiero certificar
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola, Luis! 😊 Encantado de saludarte. ¿Qué experiencia o tema te gustaría certificar?
- Cliente: ¿Las certificaciones están reconocidas por SERVIR?
  - CALIFICAR → CALIFICAR · tools ['consultar_conocimiento']
  - Leo: No tengo esa información confirmada sobre SERVIR. Si quieres, puedo derivarte con una persona de Escuela Capacita para que te lo aclaren.

### ✅ pide los métodos de pago (final COBRO)
- Cliente: Hola, soy Luis. Solo quiero certificar
  - INICIO → CALIFICAR · tools ['enviar_recurso']
  - Leo: ¡Hola, Luis! 😊 Encantado de saludarte. ¿Qué experiencia o tema te gustaría certificar?
- Cliente: Quiero la certificación individual de SIAF
  - CALIFICAR → IDENTIFICAR · tools ['elegir_camino']
  - Leo: Perfecto, Luis. ¿Quieres que te cuente cómo funciona la certificación individual de SIAF?
- Cliente: Sí, agrégala
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, Luis. Ya quedó agregada la *Certificación individual SIAF* a tu pedido por *S/ 120.00*. ¿Deseas agregar otra certificación?
- Cliente: Me llamo Luis Pérez Rojas, DNI 45678912, correo luis.perez@mail.com, celular 976543210
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: ¡Gracias, Luis! Ya están todos tus datos. ¿Deseas continuar con el siguiente paso del pedido?
- Cliente: ¿A dónde pago?
  - COBRO → COBRO · tools ['enviar_metodos_pago']
  - Leo: Estos son nuestros métodos de pago:

• *Yape*
Yape al 987654321 a nombre de Escuela Capacita.
  - Leo: Ya te envié los métodos de pago. Tu total es *S/ 120.00* y, cuando pagues, envíame por aquí la foto del comprobante.

## servicios: 8/8 (100%)

### ✅ pregunta qué hacen (final IDENTIFICAR)
- Cliente: Hola, ¿qué servicios tienen?
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 😊

Tenemos *hosting anual*, *desarrollo de página web* y *mantenimiento web mensual*. El desarrollo de página web se cotiza con una persona porque no tiene precio fijo.

¿Te interesa alguno en particular?

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
  - Leo: Listo, quedó en tu pedido el *Hosting anual* x1 por *S/ 350.00*.

Subtotal: *S/ 350.00*
Descuento: *S/ 0.00*
Total: *S/ 350.00*

¿Quieres que te ayude con algo más para tu web?

### ✅ hosting hasta el cobro (final COBRO)
- Cliente: Hola, quiero el hosting anual
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 😊

Sí, tenemos *Hosting anual* por *S/ 350.00*.

¿Quieres que te comparta más detalles o prefieres avanzar con tu pedido?
- Cliente: Sí, agrégalo
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, ya quedó en tu pedido: *Hosting anual x1* por *S/ 350.00*.

Subtotal: *S/ 350.00*
Descuento: *S/ 0.00*
Total: *S/ 350.00*

¿Quieres que te ayude con algo más de Zentek360?
- Cliente: Soy Carla Díaz, RUC 20123456789, correo carla@empresa.pe, celular 912345678
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: Gracias, Carla. Ya quedaron tus datos registrados.

¿Deseas que siga con el siguiente paso del pedido?

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

El _desarrollo de página web_ no tiene un precio fijo en el chat; se cotiza según lo que necesites. Si quieres, te ayudo a tomar los datos y lo derivo para que te den una cotización.

### ✅ precio del mantenimiento (final IDENTIFICAR)
- Cliente: Hola, ¿cuánto cuesta el mantenimiento mensual?
  - INICIO → IDENTIFICAR · tools ['buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 👋

El *mantenimiento web mensual* cuesta *S/ 150.00*.

Si quieres, también te puedo contar qué incluye o ayudarte a cotizarlo.

### ✅ pide cómo pagar el hosting (final COBRO)
- Cliente: Hola, quiero el hosting anual
  - INICIO → IDENTIFICAR · tools ['enviar_recurso', 'buscar_productos']
  - Leo: Hola, soy Leo de Zentek360 👋

Sí, tengo el *Hosting anual* disponible por *S/ 350.00*.

¿Quieres que te comparta más detalles o prefieres avanzar con tu pedido?
- Cliente: Sí, agrégalo
  - IDENTIFICAR → DATOS · tools ['buscar_productos', 'gestionar_pedido']
  - Leo: Listo, quedó agregado a tu pedido: *Hosting anual x1* por *S/ 350.00*.

Subtotal: *S/ 350.00*
Descuento: *S/ 0.00*
Total: *S/ 350.00*

¿Quieres que te ayude con algo más de Zentek360?
- Cliente: Soy Carla Díaz, RUC 20123456789, correo carla@empresa.pe, celular 912345678
  - DATOS → COBRO · tools ['registrar_datos_cliente']
  - Leo: Gracias, Carla. Ya tengo tus datos y el pedido está listo para seguir con el siguiente paso.

¿Quieres que continúe?
- Cliente: ¿Cómo pago?
  - COBRO → COBRO · tools ['enviar_metodos_pago']
  - Leo: Estos son nuestros métodos de pago:

• *Transferencia bancaria*
Transferencia BCP 191-1234567-0-12 a nombre de Zentek360 SAC.
  - Leo: Ya te envié los métodos de pago. Tu total es *S/ 350.00*.

Cuando hagas el pago, envíame por aquí la foto o captura del comprobante, por favor.
