# Spec — Configuración en capas y aprendizaje del agente

**Feature ID:** 017
**Módulo:** agente + panel del cliente + panel de plataforma
**Estado:** Aprobada en su forma (respuestas de Giannier al mapeo, 2026-10-13). Fase 1 lista para
implementar cuando se apruebe el Milestone B de la 004; Fases 2 a 4 después del piloto.
**Fecha:** 2026-10-13
**Fuentes:** `brief.md` (el brief de Giannier), `mapeo.md` (el brief contra el repo) y
`clarifications.md` (sus respuestas).

## Resumen

Cada agente de Leo debe vender como el negocio que representa y mejorar con el uso, sin que nadie
reescriba su configuración. Hoy (004) el agente ya vende por etapas, pero sus reglas siguen
entrando por dos lugares que no escalan: el texto de cada etapa, que el negocio iba a poder editar, y
un cuadro de "reglas adicionales del dueño" en texto libre, el mismo punto débil de Ventor.

Esta feature arma al agente con **cuatro capas** y le enseña con un **ciclo de aprendizaje** que
siempre pasa por una persona:

1. **Plantilla de proceso** (`productos`, `certificaciones`, `servicios`): **cómo** se vende. Etapas,
   condiciones, herramientas y reglas de cada etapa.
2. **Rubro** (restaurante, instituto, retail, servicios, salud, otros): **qué se sabe** del negocio
   típico. Reglas, preguntas frecuentes, objeciones con su manejo y situaciones sugeridas. Cada rubro
   sugiere una plantilla de proceso; el negocio puede cambiarla.
3. **Perfil del negocio**: formulario del onboarding y del panel. Nada de texto libre.
4. **Situaciones**: las reglas propias del negocio, una tarjeta por regla.

Si dos capas dicen cosas distintas, gana la más específica: **situación > perfil del negocio > rubro >
plantilla de proceso**.

El negocio puede **apagar** una regla de la plantilla o del rubro, pero no editarla. Si quiere otro
comportamiento, crea una situación. Así sigue recibiendo las mejoras que Zentek360 publique.

## Contexto de usuario

**Usuario principal:** dueño o administrador del negocio que configura su asistente.
**Otros usuarios:**
- **Revisor:** un permiso dentro de cada negocio. Aprueba lo que el agente aprende. Hoy es Giannier en
  todas las empresas del holding.
- **Operador de plataforma (Zentek360):** mantiene plantillas y rubros, y publica versiones nuevas.
- **Cliente final:** conversa por WhatsApp. No ve nada de esto.

## User stories

### Historia 1: Ver y apagar las reglas que vienen de fábrica (Fase 1)
**Como** dueño, **quiero** ver las reglas de mi plantilla y de mi rubro y apagar las que no me sirven,
**para** que el agente no haga lo que mi negocio no hace.
- WHEN abro Proceso de venta, THEN veo cada regla de la plantilla agrupada por etapa y cada regla del
  rubro, con un interruptor y sin editor de texto.
- WHEN apago una regla, THEN el siguiente mensaje del simulador ya no la aplica.
- WHEN Zentek360 publica una versión nueva, THEN las reglas nuevas llegan activas, las que apagué
  siguen apagadas y veo un aviso de novedades.

### Historia 2: Escribir mis propias reglas como tarjetas (Fase 1)
**Como** dueño, **quiero** crear situaciones con "cuándo aplica" y "qué hace el agente",
**para** enseñarle mis casos sin escribir un prompt.
- WHEN creo una situación, THEN puedo adjuntar archivos de la biblioteca, marcar que derive a una
  persona, dar un ejemplo de respuesta, limitarla a ciertas etapas o marcarla como "siempre".
- WHEN la guardo en borrador, THEN puedo probarla en el simulador sin que la vean mis clientes.
- WHEN la activo, THEN el agente la aplica a partir del siguiente mensaje.

### Historia 3: El agente solo lee lo que aplica (Fase 1)
**Como** dueño con muchas situaciones, **quiero** que el agente no se confunda,
**para** que no ignore reglas por tener demasiadas.
- WHEN llega un mensaje, THEN entran al prompt las situaciones "siempre" más las que coinciden con el
  mensaje y con la etapa, hasta un máximo configurable (5 por defecto).
- WHEN abro el modo depuración del simulador, THEN veo qué situaciones entraron y por qué.

### Historia 4: Empezar sin pantalla vacía (Fase 1)
**Como** negocio nuevo, **quiero** responder un cuestionario corto de estilo y recibir situaciones
sugeridas de mi rubro, **para** tener un agente útil desde el primer día.
- WHEN termino el wizard, THEN se me ofrecen de 3 a 5 situaciones sugeridas del rubro ("si piden
  factura") que solo activo o completo.
- WHEN elijo mi rubro, THEN se me sugiere su plantilla de proceso y puedo elegir otra.

### Historia 5: Arrancar con el estilo de mis vendedores (Fase 2)
**Como** negocio con historial, **quiero** subir chats exportados de WhatsApp con sus audios,
**para** que el agente escriba como mis vendedores.
- WHEN subo un chat y autorizo su uso, THEN los audios se transcriben, los datos de clientes se
  anonimizan y el original se borra.
- THEN salen un perfil de estilo (de los mensajes escritos), conversaciones ejemplo y argumentos y
  objeciones (también de los audios), todo como sugerencias para el revisor.

### Historia 6: El agente aprende y una persona decide (Fase 3)
**Como** revisor, **quiero** una bandeja de sugerencias que salen de las conversaciones reales,
**para** mejorar el agente sin reescribirlo.
- Fuentes: toma de control humana, venta confirmada, venta perdida (análisis semanal) y pregunta sin
  respuesta.
- WHEN abro una sugerencia, THEN veo la tarjeta propuesta, las conversaciones que la originaron y si
  choca con otra regla; puedo aprobarla, editarla y aprobarla, rechazarla o probarla en el simulador.
- Los ejemplos ganadores se aprueban en lote.
- Lo aprobado entra en vigor de inmediato; cada cambio queda en el historial y se puede deshacer.

### Historia 7: El rubro aprende y el redactor ayuda (Fase 4)
- **Como** operador de plataforma, **quiero** ver situaciones parecidas en varios negocios del mismo
  rubro y subirlas al rubro (solo la regla, nunca datos de clientes).
- **Como** dueño, **quiero** escribir una situación con mis palabras y que Leo la convierta en tarjeta y
  me avise si choca con otra o con la plantilla.

## Requerimientos funcionales

### Fase 1 · Capas y situaciones manuales (reemplaza la Fase C de la 004)
1. **[RF-001]** La plantilla de proceso conserva en código su orden de etapas, condiciones,
   herramientas y embudo (C-05 de la 004). Sus **reglas** (los textos de cada etapa, partidos en reglas
   con id estable) viven en base de datos, versionadas.
2. **[RF-002]** Cada rubro tiene en base de datos, versionados: reglas, preguntas frecuentes,
   objeciones con su manejo, situaciones sugeridas y la plantilla de proceso que sugiere.
3. **[RF-003]** Solo el operador de plataforma edita plantillas y rubros. Una versión nueva se publica
   solo si pasa los evals. Al publicarse, las reglas nuevas llegan activas a todos los negocios, salvo
   las que cada negocio apagó, y el revisor ve un aviso de novedades.
4. **[RF-004]** El negocio apaga o enciende cualquier regla de la plantilla o del rubro. No puede editar
   su texto. El apagado se recuerda por id de regla entre versiones.
5. **[RF-005]** El negocio ya no edita textos de etapa. Se retiran "editar y restaurar" de la API y del
   panel.
6. **[RF-006]** Situación: título, cuándo aplica, qué hace el agente; opcionales: archivos de la
   biblioteca, derivar a una persona, ejemplo de respuesta, etapas en las que aplica, "siempre". Estado:
   borrador, activa o inactiva. Origen: manual, sugerida del rubro, migrada (y en Fase 3, aprendida).
7. **[RF-007]** En cada mensaje entran las situaciones activas marcadas "siempre" más las que coinciden
   con el mensaje y con la etapa, hasta un máximo configurable por negocio (5 por defecto).
8. **[RF-008]** El prompt declara la prioridad situación > perfil > rubro > plantilla y presenta las
   capas en ese orden de autoridad.
9. **[RF-009]** El simulador puede probar situaciones en borrador sin activarlas para el canal real, y su
   modo depuración muestra qué reglas y situaciones entraron en cada turno.
10. **[RF-010]** Perfil del negocio por formulario: suma tratamiento (tú o usted), emojis, saludo,
    política de descuentos y cuentas de cobro (titular). El wizard deja de pedir "restricciones"
    libres.
11. **[RF-011]** Lo que ya esté escrito en `restrictions` pasa a una situación (origen migrada) y el campo
    deja de entrar al prompt.
12. **[RF-012]** Al terminar el wizard se ofrecen de 3 a 5 situaciones sugeridas del rubro para activar o
    completar.
13. **[RF-013]** El permiso **revisor** se asigna por miembro dentro de cada negocio. El dueño lo tiene por
    defecto.

### Fase 2 · Importación de chats y estilo (después del piloto)
14. **[RF-014]** Subir chats exportados de WhatsApp (texto y audios) con autorización registrada.
15. **[RF-015]** Transcribir cada audio y ubicarlo en su lugar de la conversación.
16. **[RF-016]** Anonimizar nombres, teléfonos, documentos y direcciones antes de guardar; borrar el
    original al procesarlo, también si el proceso falla.
17. **[RF-017]** Derivar perfil de estilo (solo mensajes escritos), conversaciones ejemplo y argumentos y
    objeciones (incluye audios). Todo llega como sugerencia.
18. **[RF-018]** El historial de agentes automáticos anteriores no se usa para el estilo; sí para detectar
    preguntas y objeciones frecuentes.

### Fase 3 · Ciclo de aprendizaje (después de la 007 y del piloto)
19. **[RF-019]** Sugerencias de cuatro fuentes: toma de control humana, venta confirmada (ejemplo
    ganador), venta perdida (análisis semanal) y pregunta sin respuesta (dato para el conocimiento).
20. **[RF-020]** Bandeja de sugerencias por negocio con tarjeta, conversaciones de origen y choques.
    Aprobar, editar y aprobar, rechazar, probar en el simulador. Ejemplos ganadores en lote.
21. **[RF-021]** Una sugerencia rechazada no se vuelve a proponer igual.
22. **[RF-022]** Historial de cambios de situaciones, interruptores y perfil, con deshacer.

### Fase 4 · Mejora del rubro y redactor asistido
23. **[RF-023]** El operador de plataforma ve situaciones parecidas de varios negocios del mismo rubro y
    puede proponerlas al rubro; se copia la regla, nunca datos de clientes, y pasa por evals al publicar.
24. **[RF-024]** Redactor asistido: texto libre → tarjeta, con aviso de choques con otras situaciones,
    con el perfil o con la plantilla.

## Requerimientos no funcionales
- **Aislamiento:** situaciones, interruptores, sugerencias e importaciones filtran por `tenant_id`. Las
  tablas de plantilla y rubro son de plataforma y solo las escribe el operador de plataforma.
- **Latencia:** elegir situaciones agrega como máximo una búsqueda vectorial por turno; el p95 del turno
  no sube más de 300 ms.
- **Costo del LLM:** una sola llamada de embedding por turno para el selector; ninguna llamada extra al
  modelo de chat. Transcripción, análisis semanal y redactor se miden en trazas.
- **Datos personales (Ley 29733):** autorización registrada, anonimización antes de guardar, originales
  borrados, nada de datos de clientes sube al rubro.
- **Calidad:** los evals corren al cerrar la Fase 1 y en cada publicación de plantilla o rubro.
- **Cobro:** con `BILLING_ENFORCED` apagado nada de esto tiene límite por plan.

## Fuera de alcance
- Reentrenar el modelo (fine-tuning).
- Respuestas del agente por audio, y transcribir mensajes en vivo.
- Aprendizaje que se aplique sin aprobación del revisor.
- Volver a pasar conversaciones antiguas por la configuración nueva.

## Criterios de aceptación
- [ ] Fase 1: un negocio apaga una regla de su plantilla, crea una situación, la prueba en borrador, la
      activa, y el simulador muestra que entró; una versión nueva del rubro llega con aviso y respeta lo
      apagado; los evals pasan.
- [ ] Fase 2: un chat real con audios se importa, se anonimiza, el original desaparece y salen
      sugerencias de estilo, ejemplos y objeciones.
- [ ] Fase 3: cada una de las cuatro fuentes produce una sugerencia en una conversación de prueba del
      holding; aprobar, rechazar y deshacer funcionan.
- [ ] Fase 4: una situación sube de un negocio al rubro sin datos de clientes y pasa evals.
- [ ] Cada fase la prueban las empresas del holding antes de pasar a la siguiente.

## Dependencias
- **004 motor de ventas:** pipeline por etapas, plantillas, simulador, biblioteca multimedia, pedidos.
  La Fase 1 se implementa **como la Fase C de la 004**.
- **007 conversaciones e inbox:** toma de control humana (Fase 3).
- **Despliegue con número real y piloto del holding:** conversaciones reales para las Fases 2 a 4.
