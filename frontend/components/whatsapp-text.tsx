/**
 * WhatsApp's own markup, drawn the way the customer's phone draws it.
 *
 * The agent writes `*negrita*`, `_cursiva_`, `~tachado~` and ```monoespaciado```
 * because that is what WhatsApp renders. The simulator used to print the
 * asterisks, which made a correct message look broken. Built from React nodes,
 * never from HTML strings: the text is the model's, and it is not trusted.
 */

import type { ReactNode } from "react";

// A marker must hug its text (`*a b*`, not `* a *`) and stand apart from words
// around it, as in WhatsApp: `2*3*4` is not bold.
const MARKUP = /```([\s\S]+?)```|(^|[\s(¡¿"'])([*_~])(\S(?:[^\n]*?\S)?)\3(?=$|[\s).,;:!?"'])/g;

const STYLES: Record<string, string> = {
  "*": "font-semibold",
  _: "italic",
  "~": "line-through",
};

export function WhatsAppText({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(MARKUP)) {
    const start = match.index ?? 0;
    if (match[1] !== undefined) {
      nodes.push(text.slice(last, start));
      nodes.push(
        <code key={key++} className="rounded bg-surface px-1 font-mono text-[0.85em]">
          {match[1]}
        </code>,
      );
    } else {
      const lead = match[2] ?? "";
      nodes.push(text.slice(last, start) + lead);
      nodes.push(
        <span key={key++} className={STYLES[match[3]]}>
          {match[4]}
        </span>,
      );
    }
    last = start + match[0].length;
  }
  nodes.push(text.slice(last));
  return <>{nodes}</>;
}
