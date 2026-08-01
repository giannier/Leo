import Link from "next/link";

import { AlertIcon } from "@/components/icons";

/**
 * The real 404, for a URL that is genuinely wrong.
 *
 * Separate from `ComingSoon`, and it matters that they are different: one
 * says "this does not exist", the other says "this is not built yet". Next's
 * default page says neither, in English, and reads like a crash.
 */
export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-canvas px-4 text-center">
      <AlertIcon className="size-10 text-line-strong" />
      <h1 className="mt-4 font-heading text-2xl font-semibold text-ink">
        No encontramos esta página
      </h1>
      <p className="mt-2 max-w-sm text-sm text-ink-soft">
        Puede que el enlace esté mal escrito o que la página ya no exista.
      </p>
      <Link
        href="/"
        className="mt-6 inline-flex min-h-11 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
      >
        Ir a mi panel
      </Link>
    </main>
  );
}
