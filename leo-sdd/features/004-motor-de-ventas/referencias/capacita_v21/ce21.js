// Construye el bloque de CONTEXTO DE ENTRADA.
//
// detectar_origen() ya resolvio TRES cosas por nosotros:
//   1. si el mensaje trae ad_id de Meta (senal dura),
//   2. si el texto coincide con un patron de entradas_web (respaldo),
//   3. si NINGUNA de las dos, recupera el origen guardado en contactos.
//
// El punto 3 es lo que evita que la intencion publicitaria dure un solo
// turno: el cliente puede escribir "si lo quiero" en el mensaje 2 y el
// bloque sigue apareciendo igual.

const base = $('Edit Fields1').first().json;

let r = {};
try { r = ($('detectar origen PG1').first().json || {}).r || {}; } catch (e) { r = {}; }

// Configuracion viva, editable por el equipo desde NocoDB (tabla config).
// PROHIBIDO volver a escribir un telefono o una URL mas abajo: si manana
// gerencia cambia de numero, el cliente tiene que recibir el nuevo sin
// que nadie abra n8n.
let cfg = {};
try { cfg = ($input.first().json || {}).cfg || {}; } catch (e) { cfg = {}; }

// Lecciones aprendidas: correcciones que escribe el equipo en NocoDB y
// que el agente aplica desde el turno siguiente. Es la forma segura de
// que "aprenda": lo corrige una persona, no se corrige solo.
let lecciones = '';
try { lecciones = ($input.first().json || {}).lecciones || ''; } catch (e) { lecciones = ''; }

// v21: instrucciones por etapa (tabla etapas_agente). La etapa la calcula
// el subflujo 'Calcular Etapa'; aca solo se trae el texto de cada una.
let etapas = {};
try { etapas = ($input.first().json || {}).etapas || {}; } catch (e) { etapas = {}; }

// Ultimo recurso, solo si Postgres no respondio. DEBE coincidir con
// config.telefono_gerencia_comercial. Es el UNICO lugar de todo el flujo
// donde este numero aparece escrito a mano.
const TELEFONO_GERENCIA_RESPALDO = '+51 967 285 628';

const telefono_gerencia = r.telefono_gerencia
  || cfg.telefono_gerencia_comercial
  || TELEFONO_GERENCIA_RESPALDO;

const url_tienda = cfg.url_tienda || 'https://somosescuelacapacita.com/tienda';

let contexto = '';

if (r && r.match === true) {
  const fuente = {
    anuncio:  '🎯 ANUNCIO PAGADO DE META — el cliente hizo CLICK en el anuncio',
    web:      '🔗 ENLACE DE LA WEB — el cliente llegó por un banner de la página',
    memoria:  '🎯 ORIGEN RECORDADO — este cliente YA llegó por publicidad en esta conversación'
  }[r.fuente] || '🎯 ENTRADA DIRIGIDA';

  const lineas = [
    fuente,
    '',
    'CLIENTE PRECALIFICADO: ya vio la oferta antes de escribirte.',
  ];

  if (r.referencia_id) {
    lineas.push('Producto anunciado: ' + (r.nombre || r.referencia_id)
                + ' [' + r.referencia_id + ', ' + r.tipo + ']');
    lineas.push('Estado destino sugerido: ' + r.estado_destino);
  }
  if (r.headline) lineas.push('Titular que vio el cliente: "' + r.headline + '"');
  if (r.ad_id) lineas.push('Anuncio: ' + r.ad_id);

  lineas.push('');
  lineas.push('INSTRUCCIÓN OBLIGATORIA (tiene prioridad sobre el flujo general):');
  lineas.push(r.instruccion || '');

  if (r.datos && Object.keys(r.datos).length) {
    lineas.push('');
    lineas.push('DATOS VERIFICADOS (úsalos VERBATIM; NO consultes otra herramienta para esto):');
    lineas.push(JSON.stringify(r.datos, null, 2));
  }

  contexto = lineas.join('\n');
}

return [{
  json: {
    mensaje_usuario:    base.mensaje_usuario,
    numero_telefono:    base.numero_telefono,
    numero_telefono_bd: base.numero_telefono_bd,
    user_id:            base.user_id,
    chat_key:           base.chat_key,
    conversationID:     base.conversationID,
    customerName:       base.customerName,
    contexto_entrada:   contexto,
    cliente: r.cliente || { tiene_datos:false, completo:false, faltan:['DNI','Nombres','Apellidos','Correo','Teléfono'] },
    telefono_gerencia:  telefono_gerencia,
    url_tienda:         url_tienda,
    entrada_match:      r.match === true,
    entrada_tipo:       r.tipo || '',
    entrada_ref:        r.referencia_id || '',
    entrada_fuente:     r.fuente || '',
    lecciones:          lecciones,
    cfg:                cfg,
    etapas:             etapas
  }
}];
