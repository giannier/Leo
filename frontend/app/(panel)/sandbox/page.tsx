import type { Metadata } from "next";

import { ComingSoon } from "@/components/coming-soon";

export const metadata: Metadata = { title: "Modo prueba" };

export default function Page() {
  return (
    <ComingSoon
      title="Modo prueba"
      what="Vas a poder conversar con tu agente aquí mismo, sin conectar WhatsApp y sin gastar cuota, para ajustar cómo responde antes de que hable con un cliente real."
      task="Llega en TASK-022."
    />
  );
}
