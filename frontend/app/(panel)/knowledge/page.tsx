import type { Metadata } from "next";

import { ComingSoon } from "@/components/coming-soon";

export const metadata: Metadata = { title: "Conocimiento" };

export default function Page() {
  return (
    <ComingSoon
      title="Conocimiento"
      what="La biblioteca de todo lo que tu agente sabe. Mientras tanto puedes cargarla desde el paso 4 de la configuración."
      task="Llega en TASK-023."
    />
  );
}
