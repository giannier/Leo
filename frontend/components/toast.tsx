"use client";

/**
 * A notice that says, big enough to be seen, that something was saved.
 *
 * The small "Guardado" that used to appear next to a button for two seconds went
 * unnoticed: people look at what they just typed, not at the corner. A toast sits at
 * the bottom of the screen, stays long enough to read, waits while the pointer is on
 * it, and has a close button. It is a polite live region, so a screen reader says it.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { AlertIcon, CheckIcon, CloseIcon } from "@/components/icons";
import { cx } from "@/components/ui";

type Tone = "success" | "danger";

interface ToastItem {
  id: number;
  tone: Tone;
  title: string;
  detail?: string;
}

interface Toasts {
  /** "Guardado" and, under it, what was saved. */
  success: (title: string, detail?: string) => void;
  /** A failure that must not vanish by itself. */
  error: (title: string, detail?: string) => void;
}

const SHOW_MS = 6000;

const ToastContext = createContext<Toasts | null>(null);

export function useToast(): Toasts {
  const context = useContext(ToastContext);
  // Outside the provider (a test that renders one component) saving still works.
  return context ?? { success: () => undefined, error: () => undefined };
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const push = useCallback((tone: Tone, title: string, detail?: string) => {
    const id = next.current++;
    // At most three at once; a fourth pushes the oldest out.
    setItems((current) => [...current.slice(-2), { id, tone, title, detail }]);
  }, []);

  const api = useMemo<Toasts>(
    () => ({
      success: (title, detail) => push("success", title, detail),
      error: (title, detail) => push("danger", title, detail),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex flex-col items-center gap-2 px-4"
      >
        {items.map((item) => (
          <Toast key={item.id} item={item} onClose={() => dismiss(item.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function Toast({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  const [paused, setPaused] = useState(false);
  const failure = item.tone === "danger";

  useEffect(() => {
    // A failure stays until it is closed: the person has to do something about it.
    if (failure || paused) return;
    const timer = window.setTimeout(onClose, SHOW_MS);
    return () => window.clearTimeout(timer);
  }, [failure, paused, onClose]);

  return (
    <div
      role={failure ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className={cx(
        "toast-in pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-xl border p-4 shadow-lg",
        failure ? "border-danger/30 bg-danger-soft" : "border-success/30 bg-success-soft",
      )}
    >
      <span
        className={cx(
          "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full text-white",
          failure ? "bg-danger" : "bg-success",
        )}
      >
        {failure ? <AlertIcon className="size-4" /> : <CheckIcon className="size-4" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-ink">{item.title}</p>
        {item.detail ? <p className="mt-0.5 text-sm text-ink-soft">{item.detail}</p> : null}
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="Cerrar aviso"
        className="flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-lg text-ink-faint hover:bg-surface-muted hover:text-ink"
      >
        <CloseIcon className="size-4" />
      </button>
    </div>
  );
}
