// Arma el texto que sale por WhatsApp.
//
// v21 · TARIFAS DE META: desde el 1-oct-2026 cada mensaje de texto se cobra.
// Antes un LLM (Format Chain) partia la respuesta en hasta 3 burbujas: 3
// mensajes cobrados y 1-2 llamadas extra a OpenAI por turno. Ahora va UNA
// burbuja por defecto y se parte en 2 solo si es larga. Lo hace el codigo:
// es gratis, instantaneo y siempre igual.
//
// Limites en la tabla config (NocoDB):
//   burbujas_max            1 o 2   (default 2)
//   burbuja_max_caracteres  largo a partir del cual se parte (default 700)

let cfg = {};
try { cfg = $('contexto entrada1').first().json.cfg || {}; } catch (e) { cfg = {}; }
const MAX_PARTES = Math.min(2, Math.max(1, parseInt(cfg.burbujas_max || '2', 10) || 2));
const LIMITE = Math.max(200, parseInt(cfg.burbuja_max_caracteres || '700', 10) || 700);

let texto = '';
try { texto = ($('AI Agent1').first().json || {}).output || ''; } catch (e) { texto = ''; }
texto = (texto ?? '').toString();

// 1) Fuera notas internas que a veces escribe el modelo (antes lo filtraba el Format Chain).
const NOTA_INTERNA = /^\s*(no responder|no es interacci[oó]n|mensaje vac[ií]o|mensaje no es consulta|nota interna|\[interno\])/i;
texto = texto
  .replace(/```[\s\S]*?```/g, '')                      // bloques de codigo / JSON
  .replace(/^\s*\{\s*"[\s\S]*?\}\s*$/gm, '')          // JSON suelto
  .split('\n').filter((l) => !NOTA_INTERNA.test(l)).join('\n');

// 2) Markdown -> formato de WhatsApp (igual que v19).
//    WhatsApp usa *negrita* con UN asterisco; el modelo escribe **negrita**.
const limpiar = (s) => {
  let t = (s ?? '').toString();
  if (!t.trim()) return '';
  t = t.replace(/\*\*\*(.+?)\*\*\*/g, '*$1*');
  t = t.replace(/\*\*(.+?)\*\*/g, '*$1*');
  t = t.replace(/__(.+?)__/g, '_$1_');
  t = t.replace(/^#{1,6}[ \t]*(.+)$/gm, '*$1*');
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '$1: $2');
  t = t.replace(/^[ \t]*[-+][ \t]+/gm, '• ');
  t = t.replace(/^[ \t]*>[ \t]?/gm, '');
  t = t.replace(/`{1,3}([^`]+)`{1,3}/g, '$1');
  t = t.replace(/\n{3,}/g, '\n\n');
  return t.trim();
};
texto = limpiar(texto);

// 3) Partir solo si hace falta, siempre por un salto de parrafo y lo mas cerca
//    posible de la mitad. Nunca corta una frase.
let partes = [texto];
if (MAX_PARTES === 2 && texto.length > LIMITE) {
  const parrafos = texto.split(/\n\n+/);
  if (parrafos.length > 1) {
    let corte = 1, mejor = Infinity, acumulado = 0;
    for (let i = 0; i < parrafos.length - 1; i++) {
      acumulado += parrafos[i].length + 2;
      const distancia = Math.abs(acumulado - texto.length / 2);
      if (distancia < mejor) { mejor = distancia; corte = i + 1; }
    }
    partes = [parrafos.slice(0, corte).join('\n\n'), parrafos.slice(corte).join('\n\n')];
  }
}

return [{
  json: {
    part_1: (partes[0] || '').trim(),
    part_2: (partes[1] || '').trim(),
    part_3: '',
    burbujas: partes.filter((p) => p && p.trim()).length,
  },
}];
