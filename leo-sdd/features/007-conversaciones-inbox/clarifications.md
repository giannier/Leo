# Clarifications — 007 Conversaciones e Inbox

Decisiones tomadas al construir la feature (2026-10-14), con la lectura conservadora de CLAUDE.md. Las que
tocan dinero, datos personales, contratos públicos o costo del LLM quedan **propuestas**, sin implementar.

## C-101: Web Push queda propuesto (dependencia nueva)
**Situación:** el plan pide Web Push (VAPID). Firmar las notificaciones necesita una librería nueva
(`pywebpush`, y con ella `py-vapid`), y la Constitution exige aprobación explícita para cada dependencia.
**Decisión:** no se agrega. Mientras tanto la bandeja avisa con la API de notificaciones del navegador
mientras el panel está abierto, y el correo de cola desatendida cubre el caso de nadie mirando (HU-03).
**Para ratificar:** aprobar `pywebpush` (MIT, mantenida) y las llaves VAPID en `.env`.

## C-102: "Eliminar datos del contacto" (Ley 29733) queda propuesto
**Situación:** HU-04 pide borrar los datos de un contacto a pedido del cliente. Qué se borra (nombre,
teléfono, mensajes, pedidos, comprobantes, archivos) y qué se conserva por obligaciones contables es una
decisión de **datos personales**.
**Decisión:** no se implementa sin Giannier. Propuesta: anonimizar el contacto (nombre y `custom_data`
vacíos, teléfono reemplazado por un marcador), reemplazar el texto de sus mensajes por "[eliminado a
pedido del cliente]", borrar las imágenes guardadas, y conservar los pedidos confirmados con los montos y
sin datos personales (registro contable); acción solo del dueño, auditada.

## C-103: Takeover y derivación
- Tomar una conversación derivada la saca de la cola: se limpia `derivado` (ya la atiende una persona).
- Escribir en una conversación que nadie tiene la toma; si la tiene otra persona, 409 con su nombre.
- Devolver no envía nada: el agente responde el siguiente mensaje del cliente y lee lo que escribió la
  persona (sus mensajes van al historial como mensajes del negocio).
- El takeover también calla al agente en el simulador.
- El dueño o un admin pueden "robar" una conversación (C-003); el operador no (403).

## C-104: CancelGuard
Justo antes de entregar una respuesta al proveedor se relee el takeover. Si alguien la tomó mientras el
modelo escribía, la respuesta no se envía: queda guardada como `failed` con `payload.descartado =
"takeover"` (visible en la bandeja). Los archivos o botones que una herramienta ya envió en ese turno no
se pueden retirar.

## C-105: Eventos del hilo
Se guardan como mensajes `msg_type='event'`, `author='system'` (plan). Ninguna lectura del motor los ve:
`list_for_conversation`, `latest_for_conversation` y `outbound_after` los excluyen salvo que se pidan.
Así un evento nunca se toma como respuesta del agente ni entra al historial del modelo.

## C-106: Cerrar y reabrir
Cerrar termina el hilo: el siguiente mensaje del cliente abre una conversación nueva (comportamiento de la
002). Reabrir solo se permite si el cliente no tiene otra conversación abierta (una por contacto).

## C-107: Tiempo real
WebSocket en `/api/v1/ws?token=` con el token de acceso en la URL (el navegador no puede poner cabeceras),
membresía verificada en la base. Los eventos llevan ids, nunca contenido. La presencia se publica por el
mismo canal cada 15 s y el panel la olvida a los 30 s (sin claves en Redis). Al reconectar, el panel vuelve
a leer por la API (sin reproducir eventos). Sin dependencias nuevas: `uvicorn[standard]` ya trae WebSockets.

## C-108: Auto-devolución y cola desatendida
- Auto-devolución por defecto a los 30 min sin actividad de quien la tiene; "nunca" se guarda como NULL.
  Cinco minutos antes, el panel recibe `takeover.expiring`.
- Correo de cola desatendida una sola vez por derivación, a los 10 min por defecto (`derivado.desde`,
  `derivado.avisado`).

## C-109: Vista previa de imágenes
En el canal real la imagen del cliente queda en el proveedor salvo que se haya leído como comprobante. La
bandeja muestra la vista previa solo de imágenes guardadas (simulador, comprobantes); las demás se ven en
el pedido.

## C-110: Sin librerías nuevas en el panel
Sin TanStack Query ni virtualización: la lista pagina por cursor ("Cargar más") y el hilo carga 50
mensajes con "Ver anteriores". Si el volumen real lo pide, se propone la librería.

## C-111: Lo que la bandeja enlaza en vez de repetir
- **Comprobantes:** se aprueban en la pantalla del pedido (004, TASK-432), que ya tiene la lectura del
  comprobante, las alertas y la confirmación. La bandeja muestra "Ver el pedido" cuando la conversación
  tiene uno; no duplica los botones de aprobar y rechazar.
- **Trazas del agente:** quedan en la depuración del simulador (004). En la bandeja el hilo muestra lo que
  el cliente vio; las trazas por mensaje se agregan si el piloto las pide.
- **Avisos del navegador:** con la pestaña abierta en segundo plano, el panel avisa cuando sube el número
  de conversaciones que esperan a una persona (`Notification` del navegador, permiso pedido una vez). El
  aviso con la pestaña cerrada es Web Push (C-101), pendiente de aprobar la dependencia.
- **Menú:** "Bandeja" entra a la barra inferior del teléfono (máximo cinco) y sale "Catálogo", que se abre
  desde el menú lateral o el asistente: el operador entra a la bandeja todos los días; al catálogo, poco.
