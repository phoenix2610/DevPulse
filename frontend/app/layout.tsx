import "./globals.css";
import type { Metadata } from "next";
import localFont from "next/font/local";

const sans = localFont({
  variable: "--font-sans",
  src: [
    { path: "./fonts/ibm-plex-sans-400.woff2", weight: "400" },
    { path: "./fonts/ibm-plex-sans-500.woff2", weight: "500" },
    { path: "./fonts/ibm-plex-sans-600.woff2", weight: "600" },
  ],
});
const mono = localFont({
  variable: "--font-mono",
  src: [
    { path: "./fonts/ibm-plex-mono-400.woff2", weight: "400" },
    { path: "./fonts/ibm-plex-mono-500.woff2", weight: "500" },
  ],
});

export const metadata: Metadata = { title: "DevPulse", description: "Multi-agent security and architecture review for any repository" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
