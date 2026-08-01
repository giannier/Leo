import type { Metadata } from "next";

import { ComingSoon } from "@/components/coming-soon";

export const metadata: Metadata = { title: "Catálogo" };

export default function Page() {
  return (
    <ComingSoon
      title="Catálogo"
      what="La vista completa de tus productos, con búsqueda y edición. Mientras tanto puedes cargarlos desde el paso 3 de la configuración."
      task="Llega en TASK-023."
    />
  );
}
