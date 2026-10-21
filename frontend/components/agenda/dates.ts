/**
 * Days as `YYYY-MM-DD` strings in the business's own time zone (feature 019, phase D).
 *
 * The panel may be open in another zone, so "today" and the day of an appointment come from the
 * business's zone, never from the browser's.
 */

export function todayIn(zone: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: zone }).format(new Date());
}

export function addDays(day: string, days: number): string {
  const [year, month, date] = day.split("-").map(Number);
  const moment = new Date(Date.UTC(year, month - 1, date + days));
  return moment.toISOString().slice(0, 10);
}

export function mondayOf(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  const weekday = new Date(Date.UTC(year, month - 1, date)).getUTCDay(); // 0 is Sunday
  return addDays(day, -((weekday + 6) % 7));
}

const WEEKDAYS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const MONTHS = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
];

/** `martes 27 de octubre` */
export function dayLabel(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  const weekday = new Date(Date.UTC(year, month - 1, date)).getUTCDay();
  return `${WEEKDAYS[weekday]} ${date} de ${MONTHS[month - 1]}`;
}

/** The clock part of a local time token (`2026-10-27T10:00` becomes `10:00`). */
export function clock(local: string): string {
  return local.slice(11, 16);
}

export function dayOf(local: string): string {
  return local.slice(0, 10);
}
