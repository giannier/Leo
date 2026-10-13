# Brief — Configuración en capas y aprendizaje del agente

Transcripción del brief de Giannier (PDF, 2026-10-13). Fuente de la feature 017. Está en lenguaje de
negocio a propósito; los nombres técnicos están en `mapeo.md`.

## Objetivo
Cada agente de Leo debe vender como el negocio que representa y mejorar con el uso, sin que nadie
reescriba su configuración. La idea parte de Ventor y corrige su punto débil: Ventor guarda todas las
reglas y excepciones de un negocio en un solo cuadro de texto.

## Tres capas de configuración
Un agente se arma con tres capas. Si dos capas dicen cosas distintas, gana la más específica:
situación, luego perfil, luego plantilla.

1. **Plantilla del rubro.** La mantiene Zentek360. Trae el proceso de venta típico, preguntas
   frecuentes, objeciones comunes con su manejo y reglas por defecto. Parte de las tres plantillas del
   motor de ventas: productos, certificaciones y servicios.
2. **Perfil del negocio.** Se llena en el onboarding con un formulario, no con texto libre. Incluye
   nombre del agente y del negocio, objetivo, tono (tú o usted, emojis, saludo), horarios, formas de
   pago, zonas de entrega y política de descuentos.
3. **Situaciones.** Son las reglas propias de cada negocio. Cada una es una tarjeta separada con un solo
   propósito: **Cuándo aplica** (el caso, en palabras simples), **Qué hace el agente** (la respuesta o
   acción esperada) y **Opcional** (archivos para enviar, derivar a un humano o un ejemplo de respuesta).

Cada situación se puede activar, desactivar y probar en el simulador antes de usarla.

**Regla de la plantilla.** El negocio puede apagar una regla de la plantilla, pero no editarla. Si quiere
otro comportamiento, crea una situación. Así sigue recibiendo las mejoras que Zentek360 haga a la
plantilla.

**El agente solo lee lo que aplica.** En cada mensaje usa las situaciones relacionadas con lo que
pregunta el cliente, no todas. Con demasiadas reglas a la vez, los agentes empiezan a ignorar algunas.

## Arranque del estilo
El estilo inicial sale de los vendedores reales del negocio. Si el negocio es nuevo y no tiene historial,
sale de un cuestionario.

**Con historial**
- El negocio sube chats de WhatsApp exportados con archivos, incluidos los audios.
- Cada audio se transcribe y se coloca en su lugar dentro de la conversación.
- Antes de guardar, se anonimizan los datos de los clientes: nombres, teléfonos, documentos y direcciones.
- El negocio debe autorizar el uso de esos chats.

De ese material salen tres cosas:
- **Perfil de estilo:** cómo escribe el vendedor. Sale de los mensajes escritos, no de los audios.
- **Conversaciones ejemplo:** casos reales que el agente consulta cuando aparece una situación parecida.
- **Argumentos y objeciones:** qué ofrece el vendedor y cómo responde a "está caro". Aquí sí cuentan los audios.

**Sin historial**
- El negocio responde un cuestionario corto de estilo en el onboarding.
- Se combina con la plantilla del rubro.
- El agente arranca con un estilo básico y se ajusta desde la primera corrección del revisor.

**Lo que no se usa.** El historial de agentes automáticos anteriores no sirve para el estilo, porque
copiaría a un bot. Sí sirve para detectar preguntas y objeciones frecuentes.

## Ciclo de aprendizaje
El agente propone y el revisor decide: nada de lo aprendido se aplica sin aprobación. Las reglas
aprendidas llegan con el mismo formato que una situación escrita a mano, y las ventas confirmadas llegan
como ejemplos.

Flujo: (toma de control humana | venta confirmada | venta perdida | pregunta sin respuesta) →
sugerencia en borrador → ¿aprueba el revisor? (puede editar y probar antes) → sí: queda activa y el agente
atiende con lo aprobado · no: descartada, no se vuelve a proponer. Nuevas conversaciones generan nuevas
sugerencias.

**De dónde salen las sugerencias**
1. **Toma de control humana.** Cuando un humano responde en lugar del agente, se analiza qué hizo
   distinto y se propone una situación.
2. **Venta confirmada.** La conversación se propone como ejemplo ganador, ya anonimizada.
3. **Venta perdida.** Un análisis semanal agrupa objeciones que el agente no resolvió y propone situaciones.
4. **Pregunta sin respuesta.** Si el agente no encuentra un dato, se propone agregarlo al conocimiento.

**Revisión**
- Cada negocio tiene una bandeja de sugerencias.
- Cada sugerencia muestra la tarjeta propuesta, las conversaciones que la originaron y si choca con otra regla.
- El revisor puede aprobarla, editarla y aprobarla, o rechazarla. Una sugerencia rechazada no se vuelve a proponer igual.
- Antes de aprobar, puede probarla en el simulador.

**Aplicación y control**
- Lo aprobado entra en vigor de inmediato.
- Cada cambio queda en un historial y se puede deshacer.

**Rol revisor**
- Es un permiso que se asigna dentro de cada negocio, no una persona fija.
- Hoy el revisor es Giannier en todas las empresas del holding.
- Cuando Leo se venda, el dueño asigna el rol a quien quiera: él mismo, un vendedor o un encargado.

## Mejora de plantillas y redactor asistido
**El rubro aprende.** Si la misma situación aparece en varios negocios del mismo rubro, Zentek360 puede
subirla a la plantilla. Se sube la regla, nunca los datos de clientes de un negocio.

**Redactor asistido.** El negocio escribe una situación con sus palabras ("si piden factura, que me pasen
su RUC"). Leo la convierte en tarjeta y avisa si choca con otra situación o con la plantilla.

## Fuera de alcance por ahora
- Reentrenar el modelo (fine-tuning).
- Respuestas del agente por audio.
- Aprendizaje que se aplique sin aprobación del revisor.
- Pruebas que vuelven a pasar conversaciones antiguas por la nueva configuración.

## Orden de implementación
Esta feature va después del motor de ventas, porque necesita sus pedidos confirmados y la toma de control humana.
1. Tres capas y situaciones manuales.
2. Importación de chats y estilo.
3. Ciclo de aprendizaje.
4. Mejora del rubro y redactor asistido.

Cada fase se prueba con las empresas del holding antes de pasar a la siguiente.

## Instrucciones para Claude Code
1. Mapear cada concepto a lo que ya existe en el repo; marcar lo nuevo.
2. Listar contradicciones con lo implementado o con specs anteriores (onboarding, bandeja con toma de
   control, motor de ventas).
3. Esperar la confirmación de Giannier.
4. Generar spec, plan y tasks con el flujo de Spec-Kit del repo y los nombres reales.
5. Agregar al CLAUDE.md un glosario con los términos oficiales de Leo.

## Decisiones confirmadas
- El análisis de ventas perdidas es semanal.
- Los chats importados originales se borran al procesarlos; solo se conserva la versión anonimizada.
- Los ejemplos ganadores se aprueban en lote, porque son muchos y de bajo riesgo.
