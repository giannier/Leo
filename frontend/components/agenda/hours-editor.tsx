"use client";

/**
 * Weekly opening hours (feature 019, phase D): per day, closed or up to two ranges.
 * The value is what the server stores: `{"mon": [["09:00", "13:00"], ["15:00", "19:00"]]}`.
 */

import { Checkbox } from "@/components/ui";

export type Hours = Record<string, string[][]>;

export const DAYS = [
  ["mon", "Lunes"],
  ["tue", "Martes"],
  ["wed", "Miércoles"],
  ["thu", "Jueves"],
  ["fri", "Viernes"],
  ["sat", "Sábado"],
  ["sun", "Domingo"],
] as const;

export const OFFICE_HOURS: Hours = Object.fromEntries(
  ["mon", "tue", "wed", "thu", "fri"].map((d) => [d, [["09:00", "13:00"], ["15:00", "19:00"]]]),
);

/** A readable line: `Lun a vie 09:00-13:00, 15:00-19:00; Sáb 09:00-13:00`. */
export function describeHours(hours: Hours | null | undefined): string {
  if (!hours) return "";
  const parts = DAYS.filter(([key]) => hours[key]?.length).map(
    ([key, label]) => `${label.slice(0, 3)} ${hours[key].map(([a, b]) => `${a}-${b}`).join(", ")}`,
  );
  return parts.join("; ");
}

const input = "min-h-11 rounded-lg border border-line-strong bg-surface px-2 text-base text-ink";

export function HoursEditor({ value, onChange }: { value: Hours; onChange: (next: Hours) => void }) {
  function setRange(day: string, index: number, side: 0 | 1, text: string) {
    const ranges = (value[day] ?? []).map((range) => [...range]);
    ranges[index][side] = text;
    onChange({ ...value, [day]: ranges });
  }

  function toggle(day: string, open: boolean) {
    const next = { ...value };
    if (open) next[day] = [["09:00", "13:00"]];
    else delete next[day];
    onChange(next);
  }

  return (
    <ul className="space-y-2">
      {DAYS.map(([day, label]) => {
        const ranges = value[day] ?? [];
        return (
          <li key={day} className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="w-32">
              <Checkbox label={label} checked={ranges.length > 0} onChange={(e) => toggle(day, e.target.checked)} />
            </div>
            {ranges.map((range, index) => (
              <div key={index} className="flex items-center gap-2">
                <input
                  type="time"
                  aria-label={`${label}: desde`}
                  value={range[0]}
                  onChange={(e) => setRange(day, index, 0, e.target.value)}
                  className={input}
                />
                <span className="text-ink-faint">a</span>
                <input
                  type="time"
                  aria-label={`${label}: hasta`}
                  value={range[1]}
                  onChange={(e) => setRange(day, index, 1, e.target.value)}
                  className={input}
                />
              </div>
            ))}
            {ranges.length === 1 ? (
              <button
                type="button"
                onClick={() => onChange({ ...value, [day]: [...ranges, ["15:00", "19:00"]] })}
                className="cursor-pointer text-sm font-semibold text-accent underline"
              >
                + otro tramo
              </button>
            ) : null}
            {ranges.length === 2 ? (
              <button
                type="button"
                onClick={() => onChange({ ...value, [day]: [ranges[0]] })}
                className="cursor-pointer text-sm text-ink-faint underline"
              >
                quitar tramo
              </button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
