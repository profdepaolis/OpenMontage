import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KORA STUDIO · The Art of Being",
  description: "Piattaforma di produzione audiovisiva intelligente — KORA Beach Club Policoro",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="it">
      <body className="min-h-screen bg-[#FAF7F2] text-[#1D1B18]">{children}</body>
    </html>
  );
}
