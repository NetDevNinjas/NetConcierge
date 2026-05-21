import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CodeFest 4.0 — NetConcierge",
  description: "Real-time agent activity dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
