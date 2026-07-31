import type { Metadata, Viewport } from "next";
import { Open_Sans, Poppins } from "next/font/google";

import { SessionProvider } from "@/lib/session";
import "./globals.css";

// Self-hosted by `next/font`: no request to fonts.googleapis.com, no
// render-blocking stylesheet, and no flash of invisible text on the slow
// mobile connections most of these customers are on.
const poppins = Poppins({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-poppins",
  display: "swap",
});

const openSans = Open_Sans({
  subsets: ["latin"],
  variable: "--font-open-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Leo",
    template: "%s · Leo",
  },
  description: "Tu agente de WhatsApp que atiende y vende por ti.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Zoom is deliberately left enabled. Disabling it locks out anybody who
  // needs to enlarge the text, and it is never worth the tidier layout.
  themeColor: "#0f172a",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" className={`${poppins.variable} ${openSans.variable}`}>
      <body className="antialiased">
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
