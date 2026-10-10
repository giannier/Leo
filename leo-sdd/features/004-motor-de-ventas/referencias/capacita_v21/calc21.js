// Calcula la ETAPA del proceso de venta y su columna del EMBUDO.
//
// UNICA FUENTE DE VERDAD. Lo usa el agente:
//   · antes de responder  -> para darle al prompt las instrucciones de su etapa
//   · al cerrar el turno  -> para registrar el embudo en la base central
// Si cambias una regla de etapas, cambiala SOLO aca.
//
// Todo sale de hechos que el sistema ya registro (Redis y datos_cliente),
// nunca de lo que "cree" el modelo.

const t = $('Entrada').first().json;
const chat = String(t.chat_key || '').trim();
const origen = String(t.origen || '').trim();
if (!chat) throw new Error('chat_key llego vacio.');

let claves = [];
try {
  const raw = $('Claves enviadas').first().json;
  if (Array.isArray(raw.claves))    claves = raw.claves;
  else if (Array.isArray(raw.keys)) claves = raw.keys;
  else if (Array.isArray(raw))      claves = raw;
} catch (e) { claves = []; }

const prefijo = `sent:${chat}:`;
const items = claves.map(String).filter(k => k.startsWith(prefijo)).map(k => k.slice(prefijo.length));

// Hechos
const acciones = new Set(items.filter(i => i.startsWith('personalidad:')).map(i => i.split(':')[1]).filter(Boolean));
const saludo  = acciones.has('saludo');
const cuentas = items.includes('cuentas');
const pagos   = items.filter(i => i.startsWith('pago:')).map(i => i.slice('pago:'.length));
const pago    = pagos.includes('confirmado') ? 'confirmado'
              : pagos.includes('rechazado')  ? 'rechazado'
              : pagos.includes('pendiente')  ? 'pendiente' : '';
const listas  = items.filter(i => i.startsWith('opciones:')).map(i => i.slice('opciones:'.length))
                     .filter(i => i !== 'calificacion');

// Carril: lo registra el boton o la tool `registrar carril`. Se normaliza
// porque el modelo a veces escribe 'certificación' con tilde. Si hay dos,
// gana certificacion: es el carril que se puede cerrar hoy.
const sinTilde = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const carriles = items.filter(i => i.startsWith('carril:')).map(i => sinTilde(i.slice('carril:'.length)));
let carril = carriles.some(c => c.startsWith('cert')) ? 'certificacion'
           : carriles.some(c => c.startsWith('prog')) ? 'programa' : '';

// Carrito: cantidad|precio|cert1;cert2
let carRaw = '';
try { carRaw = ($('Carrito').first().json || {}).carrito || ''; } catch (e) { carRaw = ''; }
const [carCantidad, , carItemsRaw] = String(carRaw).split('|');
const carTotal  = parseInt(carCantidad, 10) || 0;
const carItems  = String(carItemsRaw || '').split(';').map(s => s.trim()).filter(Boolean);
const carFaltan = Math.max(0, carTotal - carItems.length);

let selRaw = '';
try { selRaw = ($('Seleccion').first().json || {}).seleccion || ''; } catch (e) { selRaw = ''; }

let botStatus = '';
try { botStatus = String(($('Estado del bot').first().json || {}).bot_status || ''); } catch (e) { botStatus = ''; }

let datosCompletos = false;
try {
  const c = ($('Datos completos').first().json || {}).completo;
  datosCompletos = c === true || c === 't' || c === 'true';
} catch (e) { datosCompletos = false; }

// Si nadie registro el carril pero ya hay paquete o ya vio listas de
// certificaciones, esta en certificacion. Evita quedarse trabado en CALIFICAR.
if (!carril && (carTotal > 0 || listas.some(l => ['areas', 'paquetes', 'certificaciones'].includes(l)))) {
  carril = 'certificacion';
}

// Etapa: de lo mas avanzado a lo mas temprano. El orden importa.
const compraCert = carTotal > 0 && carFaltan === 0;   // paquete completo
const compraProg = Boolean(selRaw);                   // programa con precio comunicado
const hayCompra  = compraCert || compraProg;

let etapa;
if (pago === 'confirmado')                              etapa = 'CERRADO';
else if (pago === 'pendiente' || pago === 'rechazado')  etapa = 'VERIFICACION';
else if (!saludo)                                       etapa = 'INICIO';
else if (carTotal > 0 && carFaltan > 0)                 etapa = 'CARRITO';
else if (hayCompra && !datosCompletos)                  etapa = 'DATOS';
else if (hayCompra)                                     etapa = 'COBRO';
else if (carril === 'programa')                         etapa = 'PROGRAMA';
else if (carril === 'certificacion')                    etapa = 'IDENTIFICAR';
else if (origen)                                        etapa = 'ANUNCIO';
else                                                    etapa = 'CALIFICAR';

// Columna del embudo comun a las 19 empresas (base central de control).
const EMBUDO = {
  INICIO: 'NUEVO', CALIFICAR: 'NUEVO', ANUNCIO: 'NUEVO',
  IDENTIFICAR: 'ATENDIDO', PROGRAMA: 'DERIVADO',
  CARRITO: 'NEGOCIACION', DATOS: 'NEGOCIACION',
  COBRO: cuentas ? 'CUENTAS_ENVIADAS' : 'NEGOCIACION',
  VERIFICACION: 'EN_VERIFICACION', CERRADO: 'GANADO',
};
const derivado = botStatus.trim().toUpperCase() === 'OFF';
let embudo = EMBUDO[etapa] || 'NUEVO';
if (derivado && !['VERIFICACION', 'CERRADO'].includes(etapa)) embudo = 'DERIVADO';

return [{
  json: {
    chat_key: chat,
    etapa,
    embudo,
    carril,
    pago,
    saludo,
    cuentas,
    carrito_total: carTotal,
    carrito_faltan: carFaltan,
    datos_completos: datosCompletos,
    derivado,
    origen,
  },
}];
