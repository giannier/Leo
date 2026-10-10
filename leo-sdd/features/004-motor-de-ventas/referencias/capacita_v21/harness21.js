// Pruebas de la v21 con el código REAL de los nodos.
const fs = require('fs');
const R = (f) => fs.readFileSync(__dirname + '/' + f, 'utf8');
const calcSrc = R('calc21.js'), ceSrc = R('ce21.js'), ecSrc = R('ec21.js'), fwSrc = R('fw21.js');
const etapas = JSON.parse(R('etapas.json'));

function run(src, outs, inputJson) {
  const $ = (name) => {
    if (!(name in outs)) throw new Error('nodo no simulado: ' + name);
    return { first: () => ({ json: outs[name] }), item: { json: outs[name] } };
  };
  return new Function('$', '$input', 'console', src)($, { first: () => ({ json: inputJson }) }, { warn() {}, log() {} });
}

const CHAT = 'PE.123';
const cfg = { telefono_gerencia_comercial: '+51 967 285 628', url_tienda: 'https://tienda.test', cupon_porcentaje: '20',
              cupon_horas: '24', campus_habilitado: 'false', temas_oiep: 'inglés o PMBOK', empresa_codigo: 'capacita',
              burbujas_max: '2', burbuja_max_caracteres: '700' };
const completo = { tiene_datos: true, completo: true, faltan: [], dni: '1', nombres: 'Ana', apellidos: 'P', correo: 'a@b.c', telefono: '51999' };

function turno({ keys = [], carrito = '', seleccion = '', datos = false, origen = '', bot = '' }) {
  const calc = run(calcSrc, {
    'Entrada': { chat_key: CHAT, origen },
    'Claves enviadas': { keys: keys.map((k) => `sent:${CHAT}:${k}`) },
    'Carrito': { carrito }, 'Seleccion': { seleccion }, 'Estado del bot': { bot_status: bot },
    'Datos completos': { completo: datos },
  })[0].json;
  const detectar = { r: origen ? { match: true, fuente: 'anuncio', referencia_id: origen, tipo: 'paquete', instruccion: 'x' } : { match: false } };
  if (datos) detectar.r.cliente = completo;
  const ctx = run(ceSrc, { 'Edit Fields1': { chat_key: CHAT, user_id: CHAT, numero_telefono_bd: '51999' }, 'detectar origen PG1': detectar },
                  { cfg, lecciones: '', etapas })[0].json;
  const est = run(ecSrc, { 'contexto entrada1': ctx, 'Calcular etapa': calc, 'Redis Estado Envios1': { keys: keys.map((k) => `sent:${CHAT}:${k}`) },
                           'Redis Seleccion1': { seleccion } }, { carrito })[0].json;
  return { calc, est };
}

let fallas = 0;
const ok = (cond, msg) => { if (!cond) fallas++; console.log((cond ? 'OK  ' : 'XX  ') + msg); };
const S = 'personalidad:saludo:capacitin';
const casos = [
  ['Conversación nueva', {}, 'INICIO', 'NUEVO'],
  ['Saludó, sin carril', { keys: [S] }, 'CALIFICAR', 'NUEVO'],
  ['Viene de anuncio', { keys: [S], origen: 'PROMO-5CERT-500' }, 'ANUNCIO', 'NUEVO'],
  ['Carril por botón', { keys: [S, 'carril:certificacion'] }, 'IDENTIFICAR', 'ATENDIDO'],
  ['Carril escrito con tilde', { keys: [S, 'carril:certificación'] }, 'IDENTIFICAR', 'ATENDIDO'],
  ['Sin carril pero vio paquetes', { keys: [S, 'opciones:paquetes'] }, 'IDENTIFICAR', 'ATENDIDO'],
  ['Carril programa', { keys: [S, 'carril:programa'] }, 'PROGRAMA', 'DERIVADO'],
  ['Programa y luego certifica', { keys: [S, 'carril:programa', 'carril:certificacion'] }, 'IDENTIFICAR', 'ATENDIDO'],
  ['Paquete 5 con 2 temas', { keys: [S], carrito: '5|S/ 500.00|A;B' }, 'CARRITO', 'NEGOCIACION'],
  ['Carrito completo sin datos', { keys: [S], carrito: '2|S/ 250.00|A;B' }, 'DATOS', 'NEGOCIACION'],
  ['Carrito completo con datos', { keys: [S], carrito: '2|S/ 250.00|A;B', datos: true }, 'COBRO', 'NEGOCIACION'],
  ['Cuentas enviadas', { keys: [S, 'cuentas'], carrito: '2|S/ 250.00|A;B', datos: true }, 'COBRO', 'CUENTAS_ENVIADAS'],
  ['ING1 con precio, sin datos', { keys: [S], seleccion: 'ING1|Unico|USD 89.00|Inglés' }, 'DATOS', 'NEGOCIACION'],
  ['Pago en verificación', { keys: [S, 'cuentas', 'pago:pendiente'], carrito: '2|S/ 250.00|A;B', datos: true }, 'VERIFICACION', 'EN_VERIFICACION'],
  ['Pago observado', { keys: [S, 'pago:rechazado'] }, 'VERIFICACION', 'EN_VERIFICACION'],
  ['Pago confirmado', { keys: [S, 'pago:pendiente', 'pago:confirmado'] }, 'CERRADO', 'GANADO'],
  ['Derivado a humano a mitad de venta', { keys: [S, 'carril:certificacion'], bot: 'OFF' }, 'IDENTIFICAR', 'DERIVADO'],
  ['Derivado con pago en verificación', { keys: [S, 'pago:pendiente'], bot: 'OFF' }, 'VERIFICACION', 'EN_VERIFICACION'],
];
console.log('— Etapas (subflujo Calcular Etapa + estado conversacion1)');
for (const [nombre, args, et, em] of casos) {
  const { calc, est } = turno(args);
  const bien = calc.etapa === et && calc.embudo === em && est.etapa === et && est.estado_conversacion.includes(`ETAPA ACTUAL: ${et}`)
               && est.instrucciones_etapa.length > 50;
  ok(bien, `${nombre.padEnd(36)} -> ${calc.etapa} / ${calc.embudo}` + (bien ? '' : `  (esperado ${et}/${em}; estado=${est.etapa})`));
}
const sinCalc = run(ecSrc, { 'contexto entrada1': run(ceSrc, { 'Edit Fields1': { chat_key: CHAT }, 'detectar origen PG1': { r: {} } }, { cfg, lecciones: '', etapas })[0].json,
  'Calcular etapa': { error: 'falló' }, 'Redis Estado Envios1': { keys: [] }, 'Redis Seleccion1': {} }, { carrito: '' })[0].json;
ok(sinCalc.instrucciones_etapa.startsWith('(No se pudo calcular la etapa'), 'si el subflujo falla, el agente recibe una instrucción de respaldo');
const cobro = turno({ keys: [S], carrito: '2|S/ 250.00|A;B', datos: true }).est;
ok(cobro.instrucciones_etapa.includes('https://tienda.test') && !/\{url_tienda\}/.test(cobro.instrucciones_etapa), 'variables de config interpoladas en la etapa');
ok(cobro.burbuja_max_caracteres === '700' && cobro.cfg.empresa_codigo === 'capacita', 'config disponible para formato y embudo');

console.log('— Mensajes (Formato WhatsApp1, reemplaza al Format Chain)');
const fmt = (texto, c = cfg) => run(fwSrc, { 'contexto entrada1': { cfg: c }, 'AI Agent1': { output: texto } }, {})[0].json;
const corto = fmt('¡Hola **Ana**! 😊 ¿Qué tema necesitas certificar?');
ok(corto.burbujas === 1 && corto.part_2 === '' && corto.part_1.includes('*Ana*') && !corto.part_1.includes('**'), 'respuesta corta = 1 mensaje, negrita convertida');
const p = (n) => 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. '.repeat(n).trim();
const largo = fmt([p(4), p(4), p(4), p(4)].join('\n\n'));
ok(largo.burbujas === 2 && largo.part_1.length > 200 && largo.part_2.length > 200, `respuesta larga = 2 mensajes (${largo.part_1.length} + ${largo.part_2.length} caracteres)`);
const unoSolo = fmt([p(4), p(4), p(4), p(4)].join('\n\n'), { ...cfg, burbujas_max: '1' });
ok(unoSolo.burbujas === 1, 'con burbujas_max = 1 nunca parte');
const sinParrafos = fmt(p(30));
ok(sinParrafos.burbujas === 1, 'texto largo sin párrafos no se corta a media frase');
const notas = fmt('No responder: mensaje vacío del sistema\n¡Listo Ana! Te envié las cuentas.\n```json\n{"x":1}\n```');
ok(notas.part_1 === '¡Listo Ana! Te envié las cuentas.', 'notas internas y JSON eliminados');
const lista = fmt('Tienes:\n- Gestión Pública\n- SIAF\n\nVer [tienda](https://t.co/x)');
ok(lista.part_1.includes('• Gestión Pública') && lista.part_1.includes('tienda: https://t.co/x'), 'listas y enlaces en formato WhatsApp');
ok(fmt('').part_1 === '', 'respuesta vacía no envía nada');

console.log(fallas ? `\n${fallas} FALLAS` : '\nTodo OK');
fs.writeFileSync(__dirname + '/estado_cobro.json', JSON.stringify(cobro));
process.exit(fallas ? 1 : 0);
