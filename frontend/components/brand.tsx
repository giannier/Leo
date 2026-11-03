import Image from "next/image";

import zIcon from "./z-icon.png";

/**
 * The product's mark: Zentek360's «Z» and the name Z-BOT (Giannier, 2026-11-03, 020 C-06).
 * The colour of the name comes from the caller (`text-brand` on light, `text-brand-fg` on dark).
 * The icon is a static import, so it ships inside the build: there is no `public/` folder in the image.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-heading font-bold tracking-tight ${className ?? ""}`}>
      <Image src={zIcon} alt="" unoptimized priority style={{ height: "1.05em", width: "auto" }} />
      Z-BOT
    </span>
  );
}
