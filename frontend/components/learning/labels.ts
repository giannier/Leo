/** How the learning screens say things (feature 017, phase 3). */

export const TYPE_LABELS: Record<string, string> = {
  situacion: "Situación",
  ejemplo: "Ejemplo real",
  dato: "Dato para el asistente",
  estilo: "Forma de escribir",
};

export const SOURCE_LABELS: Record<string, string> = {
  toma_de_control: "Una persona tomó la conversación",
  venta_confirmada: "Una venta confirmada",
  venta_perdida: "Ventas que se perdieron",
  pregunta_sin_respuesta: "Una pregunta sin respuesta",
  importacion: "Tus chats importados",
};

export const ENTITY_LABELS: Record<string, string> = {
  situacion: "Situación",
  interruptor: "Regla",
  perfil: "Perfil",
  faq: "Pregunta frecuente",
  ejemplo: "Ejemplo",
  estilo: "Estilo",
};

export const FILTERS = [
  { key: "pendiente", label: "Por revisar" },
  { key: "aprobada", label: "Aprobadas" },
  { key: "rechazada", label: "Rechazadas" },
] as const;
