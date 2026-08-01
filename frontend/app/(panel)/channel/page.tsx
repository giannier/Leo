import type { Metadata } from "next";

import { ComingSoon } from "@/components/coming-soon";

export const metadata: Metadata = { title: "Mi número de WhatsApp" };

export default function Page() {
  return (
    <ComingSoon
      title="Mi número de WhatsApp"
      what="Desde aquí vas a pedir la conexión de tu número y seguir el estado de la solicitud hasta que quede activo."
      task="Llega en TASK-023."
    />
  );
}
