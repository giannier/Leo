"use client";

/**
 * Import a WhatsApp chat so the assistant learns from it (feature 017, phase 2).
 *
 * Three steps: choose the exported file, say which of the people who wrote is the business, authorise.
 * The file is read for the names without being kept; once handed over it is processed in the background
 * and deleted when that ends. What comes out are suggestions, in the inbox, that a person approves.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { useToast } from "@/components/toast";
import { Alert, Button, Card, Checkbox } from "@/components/ui";
import { api, type ChatImportRow, type ChatInspection } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

const POLL_MS = 3000;

function Status({ row }: { row: ChatImportRow }) {
  if (row.estado === "procesando") {
    return <p className="text-sm text-ink-soft">Leyendo el chat y preparando las sugerencias… puede tardar unos minutos.</p>;
  }
  if (row.estado === "fallido") {
    return <Alert tone="danger">{row.error ?? "No se pudo procesar el chat."}</Alert>;
  }
  return (
    <Alert tone="success" title="Listo">
      Salieron {row.sugerencias} sugerencias de {row.conversaciones} conversaciones
      {row.audios_transcritos ? ` (se transcribieron ${row.audios_transcritos} audios, ${row.minutos_audio.toFixed(1)} min)` : ""}.
      Revísalas abajo.
    </Alert>
  );
}

export function ChatImport({ enabled, onFinished }: { enabled: boolean; onFinished: () => void }) {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [inspection, setInspection] = useState<ChatInspection | null>(null);
  const [sellers, setSellers] = useState<string[]>([]);
  const [authorised, setAuthorised] = useState(false);
  const [running, setRunning] = useState<ChatImportRow | null>(null);
  const [past, setPast] = useState<ChatImportRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadPast = useCallback(async () => {
    try {
      const { items } = await api.assistant.imports();
      setPast(items);
      const active = items.find((row) => row.estado === "procesando");
      if (active) setRunning(active);
    } catch {
      // The panel works without the list.
    }
  }, []);

  useEffect(() => {
    void loadPast();
  }, [loadPast]);

  // Follow a chat being processed until it ends.
  useEffect(() => {
    if (!running || running.estado !== "procesando") return;
    const timer = setInterval(() => {
      api.assistant
        .importStatus(running.id)
        .then((row) => {
          setRunning(row);
          if (row.estado !== "procesando") {
            toast.success(row.estado === "listo" ? "Chat procesado" : "No se pudo procesar", row.estado === "listo" ? `${row.sugerencias} sugerencias` : "");
            void loadPast();
            onFinished();
          }
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running, loadPast, onFinished, toast]);

  function reset() {
    setFile(null);
    setInspection(null);
    setSellers([]);
    setAuthorised(false);
    if (input.current) input.current.value = "";
  }

  async function look(chosen: File) {
    setFile(chosen);
    setInspection(null);
    setError(null);
    setBusy("inspect");
    try {
      const found = await api.assistant.inspectChat(chosen);
      setInspection(found);
      // Most of the talking is usually the business's: a first guess the person can change.
      setSellers(found.participantes.slice(0, 1).map((p) => p.nombre));
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function start() {
    if (!file) return;
    setBusy("start");
    setError(null);
    try {
      setRunning(await api.assistant.startImport(file, sellers));
      reset();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  const enough = inspection ? inspection.analisis_disponibles >= inspection.analisis_necesarios : true;
  return (
    <Card
      title="Aprender de tus chats de WhatsApp"
      description="Sube un chat exportado (con sus audios si quieres) y el asistente propone cómo escriben tus vendedores, conversaciones reales como ejemplo y cómo responden a las objeciones."
    >
      <div className="space-y-4" data-testid="chat-import">
        {!enabled ? <Alert tone="info">Enciende el aprendizaje arriba para poder importar un chat.</Alert> : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {running ? <Status row={running} /> : null}

        {enabled && (!running || running.estado !== "procesando") ? (
          <>
            <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
              Chat exportado (.txt o .zip)
              <input
                ref={input}
                type="file"
                accept=".txt,.zip,text/plain,application/zip"
                onChange={(e) => {
                  const chosen = e.target.files?.[0];
                  if (chosen) void look(chosen);
                }}
                className="text-sm font-normal"
              />
              <span className="text-xs font-normal text-ink-faint">
                En WhatsApp: abre el chat → Más → Exportar chat. Hasta 40 MB.
              </span>
            </label>

            {busy === "inspect" ? <p className="text-sm text-ink-soft">Leyendo el chat…</p> : null}

            {inspection ? (
              <div className="space-y-4 rounded-lg border border-line p-4 text-sm">
                <p className="text-ink">
                  {inspection.mensajes} mensajes en {inspection.conversaciones} conversaciones
                  {inspection.audios ? ` y ${inspection.audios} audios` : ""}.
                </p>
                <fieldset className="space-y-1">
                  <legend className="font-medium text-ink">¿Quién escribe por tu negocio?</legend>
                  {inspection.participantes.map((person) => (
                    <Checkbox
                      key={person.nombre}
                      label={`${person.nombre} (${person.mensajes} mensajes)`}
                      checked={sellers.includes(person.nombre)}
                      onChange={(e) =>
                        setSellers(e.target.checked ? [...sellers, person.nombre] : sellers.filter((n) => n !== person.nombre))
                      }
                    />
                  ))}
                </fieldset>
                <p className="text-xs text-ink-faint">
                  Gasta unos {inspection.analisis_necesarios} análisis de tu tope semanal (te quedan {inspection.analisis_disponibles}).
                  Los audios se transcriben hasta 30 minutos.
                </p>
                {!enough ? (
                  <Alert tone="warning">Esta semana no te alcanzan los análisis. Sube el tope arriba o espera al lunes.</Alert>
                ) : null}
                <Checkbox
                  label="Autorizo el uso de estos chats para que el asistente aprenda de ellos."
                  hint="A los chats se les quitan los datos personales de tus clientes (nombres, teléfonos, documentos, direcciones) y el archivo original se borra en cuanto se procesa. Solo se guarda lo que apruebes."
                  checked={authorised}
                  onChange={(e) => setAuthorised(e.target.checked)}
                />
                <div className="flex gap-2">
                  <Button type="button" loading={busy === "start"} disabled={!authorised || sellers.length === 0 || !enough} onClick={() => void start()}>
                    Importar
                  </Button>
                  <Button type="button" variant="ghost" onClick={reset}>
                    Cancelar
                  </Button>
                </div>
              </div>
            ) : null}
          </>
        ) : null}

        {past.length > 0 ? (
          <details className="text-sm">
            <summary className="cursor-pointer text-ink-soft">Chats importados antes ({past.length})</summary>
            <ul className="mt-2 space-y-1 text-ink-soft">
              {past.map((row) => (
                <li key={row.id}>
                  {new Date(row.creada).toLocaleDateString("es-PE", { day: "2-digit", month: "short" })} ·{" "}
                  {row.estado === "listo" ? `${row.sugerencias} sugerencias` : row.estado === "fallido" ? `no se pudo: ${row.error ?? ""}` : "procesando"}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </Card>
  );
}
