import type { Metadata, Viewport } from "next";
import Shell from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = { title: "Metals creature lab", description: "Demo-only MT5 gold and silver strategy laboratory" };
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#1B1A2E" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&family=Silkscreen&display=swap" rel="stylesheet" />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
