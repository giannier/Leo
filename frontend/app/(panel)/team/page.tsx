import type { Metadata } from "next";

import { ComingSoon } from "@/components/coming-soon";

export const metadata: Metadata = { title: "Equipo" };

export default function Page() {
  return (
    <ComingSoon
      title="Equipo"
      what="Invitar a tu gente, darles permisos y quitarles el acceso cuando ya no trabajen contigo."
      task="Llega en TASK-023."
    />
  );
}
