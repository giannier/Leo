import Link from "next/link";

import { ClockIcon } from "@/components/icons";

/**
 * A screen that exists but is not built yet.
 *
 * The navigation shows the whole product from the first day, because a menu
 * that grows item by item makes it impossible to see the shape of what you
 * bought. The cost is links that lead nowhere - and a 404 for one of those is
 * a lie: it says something is broken when the truth is it has not been made.
 *
 * Each of these is deleted by the task that builds the real screen; the
 * `task` prop is there so it is obvious which one, and so an orphan is easy
 * to spot.
 */
export function ComingSoon({
  title,
  what,
  task,
}: {
  title: string;
  what: string;
  task: string;
}) {
  return (
    <div className="space-y-5">
      <h1 className="font-heading text-2xl font-semibold text-ink">{title}</h1>

      <div className="rounded-xl border border-dashed border-line-strong bg-surface p-8 text-center">
        <ClockIcon className="mx-auto size-10 text-line-strong" />
        <p className="mt-3 text-base font-medium text-ink">Todavía estamos construyendo esto</p>
        <p className="mx-auto mt-1.5 max-w-md text-sm text-ink-soft">{what}</p>
        <p className="mt-4 text-xs text-ink-faint">{task}</p>

        <div className="mt-6">
          <Link
            href="/onboarding"
            className="inline-flex min-h-11 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
          >
            Seguir configurando mi agente
          </Link>
        </div>
      </div>
    </div>
  );
}
