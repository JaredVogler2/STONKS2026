import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "APEX Trading Intelligence", template: "%s · APEX" },
  description: "Private swing-trading research and alert console. Research signals only; not investment advice; no order execution.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [{ media: "(prefers-color-scheme: dark)", color: "#0a0d11" }, { media: "(prefers-color-scheme: light)", color: "#f5f6f8" }],
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const jar = await cookies();
  const theme = jar.get("apex_theme")?.value === "light" ? "light" : "dark";
  const privacy = jar.get("apex_privacy")?.value === "on" ? "on" : "off";
  return (
    <html lang="en" data-theme={theme} data-privacy={privacy} suppressHydrationWarning>
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
