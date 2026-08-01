import type { Metadata } from "next";

import { ComingSoon } from "@/components/coming-soon";

export const metadata: Metadata = { title: "Planes" };

export default function Page() {
  return (
    <ComingSoon
      title="Planes"
      what="Los planes y la forma de pagar. Tu prueba de 14 días sigue corriendo mientras tanto."
      task="Llega en Feature 009."
    />
  );
}
