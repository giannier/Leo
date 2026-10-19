import { AssistantShell } from "@/components/assistant/section-nav";

/** Every Asistente page shares the side menu (Feature 018, HU-04). */
export default function AssistantLayout({ children }: { children: React.ReactNode }) {
  return <AssistantShell>{children}</AssistantShell>;
}
