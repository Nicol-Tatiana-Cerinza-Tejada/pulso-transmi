import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pulso TransMi · Observabilidad",
  description: "Dashboard público de accuracy, modelos, drift y pipeline de Pulso TransMi.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
