import type { Metadata, Viewport } from "next";
import Shell from "@/components/Shell";
import "./tokens.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Metal Lab",
  description: "Demo-only MT5 gold and silver strategy laboratory",
  icons: { icon: [{ url: "/favicon.ico", sizes: "any" }, { url: "/brand/icon-192.png", type: "image/png" }], apple: "/apple-touch-icon.png" },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#06080A" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-GB">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Mono:wght@400;500;600&family=Atkinson+Hyperlegible+Next:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Silkscreen&display=swap" rel="stylesheet" />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
