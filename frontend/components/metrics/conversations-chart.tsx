"use client";

/**
 * Conversations per day, split by who handled them (Feature 010, HU-01).
 *
 * Stacked bars: the whole bar is the day's conversations; the parts are "solo el
 * asistente" and "con una persona". Two categorical series from the validated
 * reference palette (slots 1 and 2, checked against this white surface), a
 * legend, a hover tooltip, and the same numbers as a table for whoever cannot
 * or prefers not to read the chart.
 */

import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { MetricsDay } from "@/lib/api/client";

const AGENT = "#2a78d6";
const PERSON = "#eb6834";
const GRID = "#e5e7eb";
const INK_MUTED = "#6b7280";

function short(day: string): string {
  const [, month, date] = day.split("-");
  return `${Number(date)}/${Number(month)}`;
}

export function ConversationsChart({ days }: { days: MetricsDay[] }) {
  const [table, setTable] = useState(false);
  const data = days.map((d) => ({ ...d, etiqueta: short(d.dia) }));

  return (
    <section className="space-y-2 rounded-xl border border-line bg-surface p-4" aria-label="Conversaciones por día">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold text-ink">Conversaciones por día</h2>
        <button
          type="button"
          className="text-xs font-semibold text-accent underline"
          onClick={() => setTable((on) => !on)}
        >
          {table ? "Ver gráfica" : "Ver como tabla"}
        </button>
      </div>
      {table ? (
        <div className="max-h-80 overflow-auto">
          <table className="w-full text-left text-sm" data-testid="conversations-table">
            <thead className="text-xs text-ink-soft">
              <tr>
                <th className="py-1">Día</th>
                <th>Solo el asistente</th>
                <th>Con una persona</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {days.map((d) => (
                <tr key={d.dia} className="border-t border-line">
                  <td className="py-1">{short(d.dia)}</td>
                  <td>{d.solo_agente}</td>
                  <td>{d.con_persona}</td>
                  <td>{d.conversaciones}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="h-64 w-full" data-testid="conversations-chart">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap={4}>
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="etiqueta" tick={{ fontSize: 11, fill: INK_MUTED }} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={12} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: INK_MUTED }} tickLine={false} axisLine={false} />
              <Tooltip
                cursor={{ fill: "rgba(15, 23, 42, 0.06)" }}
                contentStyle={{ borderRadius: 8, borderColor: GRID, fontSize: 12 }}
                labelStyle={{ color: "#0f172a", fontWeight: 600 }}
                itemStyle={{ color: "#0f172a" }}
              />
              <Legend
                wrapperStyle={{ fontSize: 12 }}
                iconType="square"
                formatter={(value: string) => <span style={{ color: INK_MUTED }}>{value}</span>}
              />
              <Bar dataKey="solo_agente" name="Solo el asistente" stackId="c" fill={AGENT} stroke="#ffffff" strokeWidth={1} />
              <Bar
                dataKey="con_persona"
                name="Con una persona"
                stackId="c"
                fill={PERSON}
                stroke="#ffffff"
                strokeWidth={1}
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
