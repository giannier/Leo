"use client";

/**
 * Asistente · Multimedia (TASK-441).
 *
 * The files the assistant can send: the greeting, the payment QR, a brochure.
 * Each has a key (`saludo`, `metodos_pago`...) and a "when to use it". The type
 * is judged by the file's content on the server, with WhatsApp's limits.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { AssistantHeader } from "@/components/assistant/section-nav";
import { Alert, Button, Card, Field, Skeleton, TextArea, cx } from "@/components/ui";
import { api, type MediaAsset } from "@/lib/api/client";
import { humanMessage } from "@/lib/api/errors";

/** "Flyer de paquetes 2026" → "flyer_de_paquetes_2026", the shape the server accepts. */
function suggestKey(title: string): string {
  return title
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
}

function size(bytes: number): string {
  return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;
}

export default function MediaPage() {
  const [items, setItems] = useState<MediaAsset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [key, setKey] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [when, setWhen] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const replacer = useRef<HTMLInputElement>(null);
  const [replacing, setReplacing] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems((await api.assistant.media()).items);
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function upload() {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      await api.assistant.uploadMedia(file, { clave: key, titulo: title, cuando_usar: when });
      setFile(null);
      setTitle("");
      setKey("");
      setKeyTouched(false);
      setWhen("");
      await load();
    } catch (caught) {
      setError(humanMessage(caught));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-5">
      <AssistantHeader
        title="Multimedia"
        description="Imágenes, documentos, audios y videos que tu asistente puede enviar. Usa la clave «saludo» para el saludo y «metodos_pago» para tu QR."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}

      <div className="max-w-4xl">
        <Card title="Subir un archivo">
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void upload();
            }}
          >
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                const dropped = e.dataTransfer.files[0];
                if (dropped) setFile(dropped);
              }}
              className={cx(
                "flex min-h-28 flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-4 text-center text-sm",
                dragging ? "border-accent bg-accent/5" : "border-line-strong",
              )}
            >
              <p className="text-ink-soft">
                {file ? `${file.name} · ${size(file.size)}` : "Arrastra aquí el archivo"}
              </p>
              <Button type="button" variant="secondary" onClick={() => picker.current?.click()}>
                Elegir archivo
              </Button>
              <input
                ref={picker}
                type="file"
                className="sr-only"
                accept="image/jpeg,image/png,application/pdf,video/mp4,audio/*"
                data-testid="media-file"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <p className="text-xs text-ink-faint">JPG o PNG hasta 5 MB · MP4 y audio hasta 16 MB · PDF hasta 100 MB</p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Título"
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  if (!keyTouched) setKey(suggestKey(e.target.value));
                }}
              />
              <Field
                label="Clave"
                hint="Minúsculas, números y guion bajo."
                value={key}
                onChange={(e) => {
                  setKeyTouched(true);
                  setKey(e.target.value);
                }}
              />
            </div>
            <TextArea
              label="Cuándo usarlo"
              rows={2}
              placeholder="Cuando el cliente pregunta por los paquetes"
              value={when}
              onChange={(e) => setWhen(e.target.value)}
            />
            <Button type="submit" loading={uploading} disabled={!file || !title.trim() || key.length < 2 || when.trim().length < 5}>
              Subir
            </Button>
          </form>
        </Card>
      </div>

      <input
        ref={replacer}
        type="file"
        className="sr-only"
        onChange={async (e) => {
          const next = e.target.files?.[0];
          if (!next || !replacing) return;
          setError(null);
          try {
            await api.assistant.replaceMediaFile(replacing, next);
            await load();
          } catch (caught) {
            setError(humanMessage(caught));
          } finally {
            setReplacing(null);
            e.target.value = "";
          }
        }}
      />

      {!items ? <Skeleton className="h-32 w-full" /> : null}
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
        {items?.map((asset) => (
          <li key={asset.id} className="overflow-hidden rounded-xl border border-line bg-surface" data-testid="media-card">
            {asset.tipo === "imagen" && asset.preview_url ? (
              // eslint-disable-next-line @next/next/no-img-element -- a signed, short-lived URL
              <img src={asset.preview_url} alt={asset.titulo} className="h-36 w-full object-cover" />
            ) : (
              <div className="flex h-36 items-center justify-center bg-surface-muted text-sm text-ink-faint">
                {asset.tipo}
              </div>
            )}
            <div className="space-y-1 p-3 text-sm">
              <p className="font-semibold text-ink">{asset.titulo}</p>
              <p className="font-mono text-xs text-ink-faint">{asset.clave}</p>
              <p className="text-ink-soft">{asset.cuando_usar}</p>
              <p className="text-xs text-ink-faint">
                {asset.filename} · {size(asset.size_bytes)}
              </p>
              <div className="flex gap-2 pt-1">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setReplacing(asset.id);
                    replacer.current?.click();
                  }}
                >
                  Reemplazar
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={async () => {
                    if (!window.confirm(`¿Borrar «${asset.titulo}»?`)) return;
                    await api.assistant.removeMedia(asset.id);
                    await load();
                  }}
                >
                  Borrar
                </Button>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
