// Lee las claves sent:{chat_key}:* de Redis y construye el bloque
// "ESTADO DE LA CONVERSACIÓN" que se inyecta al System Prompt en CADA turno.
//
// Esto es lo que sobrevive cuando el historial del chat se vacia por el otro
// extremo: la ventana de contexto es una cola, este bloque se reconstruye
// desde cero en cada mensaje.
//
// Formato de claves que escribe el subflujo "Enviar Media":
//   sent:{chat}:cuentas
//   sent:{chat}:flyer:{id}
//   sent:{chat}:malla:{id}
//   sent:{chat}:personalidad:{accion}:{mascota}
//   sent:{chat}:certificado:{general|oiep}
//   sent:{chat}:opciones:{calificacion|areas|paquetes|certificaciones|gerencia}
//   sent:{chat}:carril:{certificacion|programa}
//   sent:{chat}:pago:{pendiente|confirmado|rechazado}
//   sent:{chat}:seleccion            <- valor: ID|modalidad|precio

const base = $('contexto entrada1').first().json;
const origen = base.entrada_ref || '';
const cli    = base.cliente || {};
const faltan = Array.isArray(cli.faltan) ? cli.faltan : ['DNI','Nombres','Apellidos','Correo','Teléfono'];
const tel  = base.chat_key;

let claves = [];
try {
  const raw = $('Redis Estado Envios1').first().json;
  if (Array.isArray(raw.claves))      claves = raw.claves;
  else if (Array.isArray(raw.keys))   claves = raw.keys;
  else if (Array.isArray(raw))        claves = raw;
} catch (e) {
  claves = [];
}

const prefijo = `sent:${tel}:`;
const items = claves
  .map(k => String(k))
  .filter(k => k.startsWith(prefijo))
  .map(k => k.slice(prefijo.length));

const cuentas = items.includes('cuentas');

const flyers = items.filter(i => i.startsWith('flyer:')).map(i => i.slice(6).toUpperCase());
const mallas = items.filter(i => i.startsWith('malla:')).map(i => i.slice(6).toUpperCase());

// personalidad:{accion}:{mascota} — una sola pasada resuelve las cuatro
// acciones Y la mascota que quedo fijada por la primera imagen enviada.
const partes = items
  .filter(i => i.startsWith('personalidad:'))
  .map(i => i.split(':'));

const acciones       = new Set(partes.map(p => p[1]).filter(Boolean));
const saludo         = acciones.has('saludo');
const agradecimiento = acciones.has('agradecimiento');
const cupon          = acciones.has('cupon');

// El certificado ya no es una accion de personalidad: tiene su propia
// clave certificado:{general|oiep}, porque depende del aval del programa
// y no de la mascota.
const certificado = items.some(i => i.startsWith('certificado:'));

// OPCIONES INTERACTIVAS YA MOSTRADAS
//
// Esta linea es la que evita el bucle de la calificacion. Cuando el
// agente manda los botones, su mensaje va DENTRO del interactivo y no
// como texto suelto, asi que puede quedar debil en el historial. El
// bloque de ESTADO no depende del historial: si aca dice que ya se
// pregunto, no se vuelve a preguntar y punto.
const calificacionEnviada = items.includes('opciones:calificacion');

// CARRIL ELEGIDO
//
// Se guarda cuando el cliente toca un boton de la calificacion. Es el
// dato que decide toda la conversacion, asi que no puede depender de
// que el historial lo recuerde.
//
// v21: el carril, la etapa y el embudo los calcula el subflujo 'Calcular Etapa'
// (unica fuente de verdad, el mismo que registra el embudo al cerrar el turno).
let calc = {};
try { calc = $('Calcular etapa').first().json || {}; } catch (e) { calc = {}; }
const carril = calc.carril || '';

// ESTADO DEL PAGO
//
// Lo escribe el subflujo que avisa a Gerencia y, al confirmar, el
// webhook del correo. Es lo que evita que el agente le vuelva a pedir
// el comprobante a alguien que ya pagó.
const pagoEstados = items
  .filter(i => i.startsWith('pago:'))
  .map(i => i.slice('pago:'.length));
const pago = pagoEstados.includes('confirmado') ? 'confirmado'
           : pagoEstados.includes('rechazado')  ? 'rechazado'
           : pagoEstados.includes('pendiente')  ? 'pendiente'
           : '';

// Listas que ya se le mostraron, para no repetirle la misma pantalla.
const listasVistas = items
  .filter(i => i.startsWith('opciones:'))
  .map(i => i.slice('opciones:'.length))
  .filter(i => i !== 'calificacion');

const mascota = (partes.map(p => p[2]).filter(Boolean)[0] || '');
const personalidad = mascota ? mascota.charAt(0).toUpperCase() + mascota.slice(1) : '';

// Si aún no hay ninguna fijada, la asigna el sistema con un hash estable
// del chat_key. Mismo cliente = misma mascota siempre, reparto ~50/50.
// El LLM no elige: siempre prefiere la primera opción que lee.
let h = 0;
for (const ch of String(tel)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
const asignada = personalidad || (h % 2 === 0 ? 'Capacitin' : 'Capacitina');

// La seleccion ya no vive en el nombre de la clave sino en su valor, para
// que al cambiar de modalidad se sobrescriba en vez de acumularse.
let selRaw = '';
try { selRaw = ($('Redis Seleccion1').first().json || {}).seleccion || ''; } catch (e) { selRaw = ''; }
const [selId, selModalidad, selPrecio, selNombre] = String(selRaw).split('|');

// CARRITO DE CERTIFICACIONES
//
// Formato de la clave sent:{chat}:carrito -> cantidad|precio|c1;c2;c3
// Se guarda entero y se sobrescribe en cada cambio: asi no hay forma
// de que queden restos de una eleccion anterior.
let carRaw = '';
try { carRaw = ($input.first().json || {}).carrito || ''; } catch (e) { carRaw = ''; }

const [carCantidad, carPrecio, carItemsRaw] = String(carRaw).split('|');
const carItems = String(carItemsRaw || '')
  .split(';')
  .map(s => s.trim())
  .filter(Boolean);
const carTotal = parseInt(carCantidad, 10) || 0;
const carFaltan = Math.max(0, carTotal - carItems.length);


// ENLACE DIRECTO A GERENCIA COMERCIAL
//
// Decirle al cliente "escribe al +51 ..." lo obliga a copiar el numero,
// guardarlo, buscar el contacto y redactar de cero: cinco pasos en el
// momento de mayor intencion de compra. Ahi se pierde la venta.
//
// Este enlace abre el chat con el mensaje ya escrito. Y lo arma el
// sistema, no el LLM: los modelos codifican mal los caracteres de una
// URL y un enlace roto es peor que ningun enlace.
const telDigitos = String(base.telefono_gerencia || '').replace(/\D/g, '');
const nombreCliente = [cli.nombres, cli.apellidos].filter(Boolean).join(' ').trim();

const partesMensaje = ['Hola, vengo del asistente de Escuela Capacita.'];
if (nombreCliente) {
  partesMensaje.push('Soy ' + nombreCliente + (cli.dni ? ' (DNI ' + cli.dni + ')' : '') + '.');
}
if (carTotal && carItems.length) {
  partesMensaje.push('Quiero el paquete de ' + carTotal + ' certificaciones por ' + carPrecio
    + ': ' + carItems.join(', ') + '.');
} else if (selRaw) {
  const queInteresa = selNombre || selId;
  partesMensaje.push('Me interesa ' + queInteresa
    + (selModalidad ? ' - ' + selModalidad : '')
    + (selPrecio ? ' (' + selPrecio + ')' : '') + '.');
} else {
  partesMensaje.push('Quisiera mas informacion.');
}

const link_gerencia = telDigitos
  ? 'https://wa.me/' + telDigitos + '?text=' + encodeURIComponent(partesMensaje.join(' '))
  : '';


// ===========================================================================
// v21 · ETAPA DEL PROCESO (calculada en el subflujo 'Calcular Etapa')
// ===========================================================================
const cfg = base.cfg || {};
const etapasTxt = base.etapas || {};
const campusHabilitado = String(cfg.campus_habilitado ?? 'false').trim().toLowerCase() === 'true';
const etapa  = String(calc.etapa || '');
const embudo = String(calc.embudo || '');

// Variables que se pueden usar en las instrucciones de la tabla: {nombre}
const vars = {
  cupon_porcentaje: String(cfg.cupon_porcentaje || '20'),
  cupon_horas: String(cfg.cupon_horas || '24'),
  link_gerencia: link_gerencia || ('wa.me/' + telDigitos),
  telefono_gerencia: String(base.telefono_gerencia || ''),
  url_tienda: String(base.url_tienda || cfg.url_tienda || ''),
  temas_oiep: String(cfg.temas_oiep || 'la certificación de inglés'),
};
const interpolar = (s) => String(s || '').replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));

let instrucciones_etapa;
if (!etapa) {
  instrucciones_etapa = '(No se pudo calcular la etapa en este turno. Atiende con las herramientas '
    + 'disponibles siguiendo el bloque ESTADO y, si no sabes cómo avanzar, pasa al cliente con '
    + 'Gerencia Comercial usando el enlace directo.)';
  console.warn('Calcular Etapa no devolvio etapa', JSON.stringify(calc));
} else if (etapasTxt[etapa]) {
  instrucciones_etapa = interpolar(etapasTxt[etapa]).trim();
} else {
  instrucciones_etapa = `(No hay instrucciones cargadas para la etapa ${etapa} en la tabla etapas_agente. `
    + 'Atiende con las herramientas disponibles y, si no sabes cómo avanzar, pasa al cliente '
    + 'con Gerencia Comercial usando el enlace directo.)';
  console.warn('etapas_agente sin fila activa para', etapa);
}

let lineaPaquete;
if (carTotal) {
  lineaPaquete = `- PAQUETE ELEGIDO: ${carTotal} certificación(es) por ${carPrecio}. Este es el precio que YA le comunicaste: úsalo tal cual, no lo recalcules.`;
} else {
  lineaPaquete = '- PAQUETE: ⬜ el cliente aún no eligió cuántas certificaciones lleva.';
}

let lineaCarrito;
if (carItems.length) {
  lineaCarrito = `- CARRITO: ${carItems.length} de ${carTotal || '?'} elegidas:\n`
    + carItems.map((c, i) => `    ${i + 1}. ${c}`).join('\n')
    + (carFaltan
        ? `\n  ⚠️ FALTAN ${carFaltan}. Pregúntale cuáles más quiere ANTES de pedirle los datos o cobrar.`
        : '\n  ✅ Carrito completo. Ya puedes pasar a los datos y al pago.');
} else {
  lineaCarrito = '- CARRITO: ⬜ vacío, todavía no eligió ninguna certificación.';
}

const lineas = [
  `- ETAPA ACTUAL: ${etapa || '(sin calcular)'} (calculada por el sistema; NO la cambies).`,
  pago === 'confirmado'
    ? '- PAGO: ✅ CONFIRMADO por Gerencia. Ya se le avisó al cliente y sus certificados están en emisión. ⛔ NO le pidas el comprobante otra vez ni le mandes las cuentas de pago. Si escribe, atiéndelo sobre la emisión de sus certificados.'
    : pago === 'rechazado'
      ? '- PAGO: ⚠️ OBSERVADO por Gerencia. Hay un detalle con su comprobante. Ayúdalo a resolverlo y, si no puedes, pásalo con Gerencia Comercial.'
      : pago === 'pendiente'
        ? '- PAGO: ⏳ EN VERIFICACIÓN. Ya avisamos a Gerencia. ⛔ NO le pidas el comprobante de nuevo ni le prometas que ya está confirmado: el sistema le avisa solo cuando Gerencia lo verifique. Si pregunta, dile que está en revisión.'
        : '- PAGO: ⬜ todavía no envió comprobante.',
  carril === 'certificacion'
    ? '- CARRIL: 🟢 CERTIFICACIÓN RÁPIDA. El cliente ya eligió: solo quiere certificar experiencia que ya tiene. Sigue la ETAPA ACTUAL. NO le ofrezcas el programa de campus.'
    : carril === 'programa'
      ? '- CARRIL: 🔵 PROGRAMA COMPLETO. El cliente quiere estudiar. Sigue la ETAPA ACTUAL.'
      : '- CARRIL: ⬜ todavía no eligió.',
  listasVistas.length
    ? `- LISTAS YA MOSTRADAS: ${listasVistas.join(', ')}. Si te pide "qué más tienes", muéstrale algo DISTINTO o pídele un tema concreto. Repetir la misma pantalla lo hace abandonar.`
    : '- LISTAS: ⬜ todavía no le mostraste ninguna.',
  calificacionEnviada
    ? '- CALIFICACIÓN: ✅ YA se la preguntaste y el cliente ya vio los botones. ⛔ PROHIBIDO volver a llamar a \`mostrar opciones\` con tipo=calificacion. Su último mensaje ES la respuesta: si el CARRIL todavía figura vacío, regístralo con `registrar carril`.'
    : '- CALIFICACIÓN: ⬜ aún no se la preguntaste.',
  lineaPaquete,
  lineaCarrito,
  personalidad
    ? `- PERSONALIDAD FIJADA: ${personalidad}. Ya la usaste en esta conversación. Escribe SIEMPRE con el tono y los emojis de ${personalidad}. PROHIBIDO cambiarla a mitad de conversación, por muy avanzada que esté.`
    : `- PERSONALIDAD ASIGNADA: ${asignada}. El sistema la eligió para este cliente. NO la elijas tú, NO la cambies. Escribe con el tono y los emojis de ${asignada}.`,
  origen
    ? `- ORIGEN: 🎯 este cliente llegó por publicidad de ${origen}. Es tráfico PAGADO. No lo trates como consulta general: tu objetivo es cerrar ESA venta. Lee el bloque CONTEXTO DE ENTRADA.`
    : '- ORIGEN: orgánico (no viene de anuncio).',
  saludo
    ? '- SALUDO: ✅ YA ENVIADO. PROHIBIDO llamar a \`enviar personalidad\` con accion=saludo. PROHIBIDO volver a saludar por texto. PROHIBIDO volver a pedir el nombre.'
    : '- SALUDO: ⬜ aún no enviado.',
  flyers.length
    ? `- FLYERS YA ENVIADOS: ${flyers.join(', ')}. PROHIBIDO volver a llamar a \`enviar flyer\` para estos IDs, bajo NINGUNA circunstancia (ni al elegir modalidad, ni al dar el precio, ni al comparar con una promo, ni al cerrar la venta).`
    : '- FLYERS: ⬜ ninguno enviado aún.',
  mallas.length
    ? `- MALLAS YA ENVIADAS: ${mallas.join(', ')}. No las repitas.`
    : '- MALLAS: ⬜ ninguna enviada aún.',
  certificado
    ? '- MODELO DE CERTIFICADO: ✅ YA ENVIADO. Si el cliente vuelve a pedirlo, dile que revise el mensaje anterior. NO lo reenvíes.'
    : '- MODELO DE CERTIFICADO: ⬜ aún no enviado.',
  selRaw
    ? `- SELECCIÓN REGISTRADA: programa ${selId} · modalidad ${selModalidad} · precio ${selPrecio}. Estos son los valores que YA le comunicaste al cliente. Úsalos EXACTAMENTE así al registrar el pago o al derivar a Gerencia. No los recalcules, no los redondees, no vuelvas a preguntar la modalidad.`
    : '- SELECCIÓN: ⬜ el cliente aún no tiene programa y modalidad confirmados.',
  cli.completo
    ? `- DATOS DEL CLIENTE: ✅ COMPLETOS — ${cli.nombres} ${cli.apellidos}, DNI ${cli.dni}, ${cli.correo}, ${cli.telefono}. NO se los vuelvas a pedir. Ya puedes enviar las cuentas de pago o derivar a Gerencia.`
    : `- DATOS DEL CLIENTE: ⬜ FALTAN ${faltan.join(', ')}.`
      + (cli.tiene_datos ? ` Ya tienes: ${['dni','nombres','apellidos','correo','telefono'].filter(k => cli[k]).map(k => k + '=' + cli[k]).join(', ')}. NO los vuelvas a pedir.` : '')
      + ' ⛔ PROHIBIDO llamar a \`enviar cuentas de pago\` o derivar a Gerencia hasta tenerlos TODOS.',
  cuentas
    ? '- CUENTAS DE PAGO: ✅ YA ENVIADAS. PROHIBIDO llamar a \`enviar cuentas de pago\` otra vez. El cliente ya tiene la imagen: si pregunta, dile que revise el mensaje anterior. Lo que toca ahora es pedirle la foto del comprobante.'
    : '- CUENTAS DE PAGO: ⬜ aún no enviadas.',
  cupon
    ? '- CUPÓN: ✅ ya ofrecido.'
    : '- CUPÓN: ⬜ aún no ofrecido.',
  agradecimiento
    ? '- AGRADECIMIENTO: ✅ ya enviado.'
    : '- AGRADECIMIENTO: ⬜ aún no enviado.'
];

const estado = ['Hechos registrados por el sistema. No los contradigas:', ''].concat(lineas).join('\n');

return [{
  json: {
    ...base,
    estado_conversacion: estado,
    datos_completos: cli.completo === true,
    personalidad_fijada: personalidad,
    personalidad_asignada: asignada,
    seleccion_id: selId || '',
    seleccion_modalidad: selModalidad || '',
    seleccion_precio: selPrecio || '',
    seleccion_nombre: selNombre || '',
    carrito_cantidad: carTotal,
    carrito_precio: carPrecio || '',
    carrito_items: carItems,
    carrito_faltan: carFaltan,
    carrito_completo: carTotal > 0 && carFaltan === 0,
    calificacion_enviada: calificacionEnviada,
    carril,
    pago,
    listas_vistas: listasVistas,
    link_gerencia,
    etapa,
    embudo,
    instrucciones_etapa,
    campus_habilitado: campusHabilitado,
    cupon_porcentaje: vars.cupon_porcentaje,
    cupon_horas: vars.cupon_horas,
    temas_oiep: vars.temas_oiep,
    burbuja_max_caracteres: String(cfg.burbuja_max_caracteres || '700')
  }
}];
