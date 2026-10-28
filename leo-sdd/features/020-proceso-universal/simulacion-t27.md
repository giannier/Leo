# T2.7 · Conversaciones simuladas del proceso universal (sin la API de OpenAI)

**Fecha:** 2026-10-28 · **Quién:** Claude, haciendo de agente · **Gasto en la API:** cero (D5 no aprobado).

## Cómo se hizo

Un arnés (`backend/evals/simulacion/harness.py`) corre el sistema real —motor de etapas, prompt, herramientas, motor de
pedidos, agenda y base de datos— dentro de una transacción que siempre se deshace. Donde respondería el modelo, responde
quien simula: lee la llamada exacta que recibiría el modelo (herramientas, prompt, historial) y escribe la respuesta o las
herramientas que llamaría. Los escenarios quedan en `backend/evals/simulacion/escenarios/` para repetirlos.

**Lo que mide:** si el prompt, las herramientas y los datos llevan a un agente que sigue las instrucciones a cerrar cada tipo
de venta; si las herramientas responden lo correcto; si el texto que ve el modelo es coherente.

**Lo que no mide:** cómo lo lee el modelo real (gpt-5.4-mini). Quien simula sigue las instrucciones con más criterio que un
modelo pequeño, así que un flujo que aquí cierra **no prueba** que cierre en producción. Tampoco mide la elección de
situaciones por significado (necesita los vectores de la API de embeddings): en cada turno se indicó a mano cuáles entrarían.
La validación con el modelo real (T5.1) sigue pendiente del presupuesto.

## Los cinco negocios y cómo cerraron

| Negocio (rubro) | Qué se vende | Cierre probado | Resultado |
|---|---|---|---|
| Ferretería El Tornillo (hogar) | Herramientas, pintura, kit de 3 por categoría, instalación a cotizar | Pedido con paquete por categoría → datos → confirmación → pago | Cierra en 5 turnos |
| Spa Raíces (belleza) | Faciales y masajes con agenda, crema | Cita agendada + pedido en la misma conversación → pago | Cierra en 4 turnos |
| Confecciones Lumen (b2b) | Uniformes por volumen | Cotización derivada con los datos | Deriva en 1 turno |
| Confecciones Lumen (b2b) | Diseño de colección | «Lo arma una persona»: explica, 1 pregunta, deriva | Deriva en 2 turnos |
| Instituto Aprende Perú (educación, plantilla aplicada) | Certificados, pack de 3 con tema libre, diplomado | Camino «certificar» → pack → datos (DNI, correo) → pago; diplomado derivado sin frenar el pago | Cierra en 5 turnos |

Transcripciones completas: [simulacion-t27-transcripciones.md](simulacion-t27-transcripciones.md).

## Lo que encontró y cómo quedó

Ordenado por gravedad. Todo lo corregido tiene pruebas automáticas.

1. **Siete rubros mandaban derivar toda reserva a una persona, por encima de la agenda.** Belleza, salud, servicios, hogar,
   inmobiliaria, automotriz y turismo decían «toma día y hora y deriva; no confirmes horarios por tu cuenta», mientras el
   bloque AGENDA dice «agenda y NO derives», y el prompt pone el RUBRO por encima del PROCESO. Un modelo obediente nunca
   agendaba. **Esto ya pasa hoy en producción** con cualquier negocio con agenda en esos rubros. *Corregido:* migración 0074,
   versión nueva de cada rubro con la regla condicionada («si tienes agenda, reserva con ella; si no, deriva»). Misma clave:
   quien la apagó la sigue teniendo apagada; quien la escribió con sus palabras conserva su texto y la ve como «mejorada».
2. **El paquete por categoría no reconocía nombres cortos.** «Kit con martillo, wincha y alicate» dejaba el kit vacío:
   solo aceptaba el nombre exacto («Martillo de uña 16 oz»). *Corregido:* en un paquete cerrado, si lo que dijo el cliente
   señala un solo producto elegible, se toma; si señala varios, se ofrecen esos primero; un número o una letra que el nombre
   no tiene no se acepta («wincha de 8 m» no toma la de 5 m). Un paquete con temas libres guarda lo que escribió el cliente.
3. **Al completar los datos, el agente podía prometer los métodos de pago sin enviarlos.** La herramienta decía «sigue con el
   siguiente paso», pero en esa etapa no está la de métodos de pago (la etapa cambia en el turno siguiente). *Corregido (solo
   proceso universal):* al completarse los datos con el pedido completo, pide resumir el pedido y confirmar; el «sí» del
   cliente abre el pago. Sin llamadas extra al modelo.
4. **La búsqueda fallaba con frases y tildes.** «pintura pared», «certificación almacén» y «crema aloe» no encontraban nada y
   el agente recibía el catálogo entero (con cientos de productos, lo buscado ni aparecería). *Corregido:* si la frase no
   coincide entera, se busca por raíces de palabras sin tildes; si un producto tiene todas, es coincidencia; si solo algunas,
   se dice que es parcial.
5. **Lo que «arma una persona» se veía como «S/ 0.00».** Un modelo podía decir que es gratis. *Corregido:* «precio con un
   asesor» (o «desde S/ X» si tiene precio de referencia), en la búsqueda y en las opciones.
6. **La agenda no podía buscar «el jueves por la tarde».** Solo aceptaba un día y ofrecía las 9:00. *Corregido:* `desde`
   admite una hora (`2026-10-29T14:00`) y se descartan las anteriores de ese día.
7. **COBRO mostraba a la vez pago previo y contra entrega.** A un negocio contra entrega se le nombraba una herramienta que
   no tiene. *Corregido (solo proceso universal):* cada línea dice de qué forma de pago es y solo queda la del negocio.
8. **La situación «Saludar y pedir el nombre» casi nunca habría entrado.** Se elige por parecido con lo que escribe el
   cliente, y trata del momento, no de sus palabras. *Corregido:* las situaciones sugeridas de una plantilla pueden traer
   etapas y «siempre»; esta entra siempre, pero solo en el primer mensaje.
9. **Menores, corregidos:** la etapa VERIFICACIÓN del universal y un mensaje de error nombraban la acción
   `cambiar_cantidad`, que no existe (es `cantidad`); en el ESTADO, un tema largo del paquete se cortaba a mitad de palabra.

## Lo que queda abierto

- **Validación con el modelo real (T5.1):** sigue bloqueada por el presupuesto (D5). Todo lo de esta feature que cambia el
  prompt, incluida la 0074, necesita esa pasada antes de llegar a producción.
- **Situaciones que piden derivar durante el cobro:** en el instituto, «quiere estudiar un programa» pide derivar. Quien
  simuló envió primero los métodos de pago y derivó después; un modelo pequeño podría derivar antes y dejar el pago para la
  persona. Es un punto a mirar en la pasada real.
- **El rubro «hogar» es de servicios del hogar** (gasfitería, electricidad); una ferretería encaja mejor en «retail». Es
  contenido de plantillas por rubro, pendiente de saber los rubros de las 20 empresas.
- **Tras derivar, el asistente calla** aunque el cliente pregunte otra cosa (diseño de uniformes, turno 2). Es lo acordado:
  la conversación queda con la persona.
