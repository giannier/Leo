import { AssistantShell } from "@/components/assistant/section-nav";

/** Productos is part of the assistant: same side menu as Asistente (Feature 018, HU-04). */
export default function AssistantLayout({ children }: { children: React.ReactNode }) {
  return <AssistantShell>{children}</AssistantShell>;
}
