import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Leo",
  description: "Multi-tenant WhatsApp AI agent platform.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
