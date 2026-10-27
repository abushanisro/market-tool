import type { Metadata, Viewport } from "next";
import "./globals.css";
import Header from "@/components/Header";
import { searchIndex } from "@/lib/spec";

export const metadata: Metadata = {
  title: { default: "Emithran API Reference", template: "%s • API Reference • Emithran" },
  description: "REST API for the Emithran manufacturing taxonomy, materials, machines, processes and market intelligence.",
  icons: { icon: "/favicon.png" },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

const themeScript = `try{var t=localStorage.getItem("emithran-theme");if(!t&&matchMedia("(prefers-color-scheme: dark)").matches)t="dark";document.documentElement.dataset.theme=t||"light"}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&family=Sora:wght@600;700&display=swap" />
      </head>
      <body>
        <Header index={searchIndex()} />
        {children}
      </body>
    </html>
  );
}
