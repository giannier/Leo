"use client";

/**
 * Save-as-you-type for the wizard steps.
 *
 * HU-02: "WHEN completo un paso, THEN queda persistido aunque cierre el
 * navegador." The person filling this in is a shop owner between customers -
 * they will be interrupted, and they will close the tab. A "Save" button they
 * did not reach is a form they have to fill in twice.
 *
 * Three things make that promise real, and all three are easy to leave out:
 *
 * 1. **Debounce, then flush.** Saving per keystroke is a request per letter.
 *    Debouncing alone loses the last edit whenever somebody types and
 *    immediately navigates - which is exactly what "next step" is - so the
 *    pending write is flushed on unmount too.
 * 2. **A warning before the tab closes** while a write is still in flight.
 * 3. **`expected_updated_at`.** The backend chose last-write-wins over
 *    locking (Clarification 008), so a save never fails; it reports
 *    `overwrote_newer` instead. Sending the version we last saw is the only
 *    way anyone ever learns a colleague's edit was overwritten.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { humanMessage } from "./api/errors";

export type SaveState =
  | { status: "idle" }
  | { status: "pending" }
  | { status: "saving" }
  | { status: "saved"; at: number; overwroteNewer: boolean }
  | { status: "error"; message: string };

/** Long enough to swallow a burst of typing, short enough to feel automatic. */
const DEBOUNCE_MS = 800;

interface Options<TValues, TResult> {
  /** Sends the change. Receives the values and the version we last saw. */
  save: (values: TValues, expectedUpdatedAt: string | null) => Promise<TResult>;
  /** Pulls the new version out of the response, for the next save. */
  versionOf: (result: TResult) => string | null;
  /** True when the server says this write landed on top of a newer one. */
  overwroteNewer?: (result: TResult) => boolean;
  /** Called after a successful save, e.g. to refresh the checklist. */
  onSaved?: (result: TResult) => void;
}

export function useAutosave<TValues, TResult>({
  save,
  versionOf,
  overwroteNewer,
  onSaved,
}: Options<TValues, TResult>) {
  const [state, setState] = useState<SaveState>({ status: "idle" });

  const version = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queued = useRef<TValues | null>(null);
  const inFlight = useRef(false);
  // Kept in refs so the flush-on-unmount effect does not need them as
  // dependencies - listing them would tear the listener down and rebuild it
  // on every keystroke.
  const saveRef = useRef(save);
  const versionOfRef = useRef(versionOf);
  const overwroteRef = useRef(overwroteNewer);
  const onSavedRef = useRef(onSaved);
  saveRef.current = save;
  versionOfRef.current = versionOf;
  overwroteRef.current = overwroteNewer;
  onSavedRef.current = onSaved;

  /** Tell the hook which version the server currently holds. */
  const seed = useCallback((updatedAt: string | null) => {
    version.current = updatedAt;
  }, []);

  const flush = useCallback(async () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const values = queued.current;
    if (values === null || inFlight.current) return;

    queued.current = null;
    inFlight.current = true;
    setState({ status: "saving" });

    try {
      const result = await saveRef.current(values, version.current);
      version.current = versionOfRef.current(result);
      setState({
        status: "saved",
        at: Date.now(),
        overwroteNewer: overwroteRef.current?.(result) ?? false,
      });
      onSavedRef.current?.(result);
    } catch (caught) {
      // The values stay in the form, so nothing the person typed is lost -
      // and the error says the change is not stored yet, which is the part
      // they need to know before they close the tab.
      setState({ status: "error", message: humanMessage(caught) });
    } finally {
      inFlight.current = false;
      // Something arrived while this was in the air.
      if (queued.current !== null) {
        void flush();
      }
    }
  }, []);

  /** Queue a change. Call it on every edit; the hook does the rest. */
  const schedule = useCallback(
    (values: TValues) => {
      queued.current = values;
      setState({ status: "pending" });
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void flush(), DEBOUNCE_MS);
    },
    [flush],
  );

  // Warn before the tab closes with an unsaved change. Only while there is
  // one: an unconditional handler is the dialogue everybody has learnt to
  // dismiss without reading.
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (queued.current !== null || inFlight.current) {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  // Flush on unmount. Moving to the next step unmounts the current one, and
  // without this the last thing typed - often the whole field - is dropped.
  useEffect(() => {
    const pending = timer;
    return () => {
      if (queued.current !== null) {
        // `flush` clears the timer itself; this covers the case where there
        // is nothing queued but a timer is still armed, so it cannot fire
        // into a component that no longer exists.
        void flush();
      } else if (pending.current) {
        clearTimeout(pending.current);
      }
    };
  }, [flush]);

  return { state, schedule, flush, seed };
}

/** The sentence shown next to a step's heading. */
export function saveLabel(state: SaveState): string | null {
  switch (state.status) {
    case "pending":
    case "saving":
      return "Guardando...";
    case "saved":
      return state.overwroteNewer
        ? "Guardado. Ojo: alguien más había hecho cambios y los reemplazaste."
        : "Guardado";
    case "error":
      return state.message;
    default:
      return null;
  }
}
