import "@fontsource-variable/ibm-plex-sans";
import "@fontsource-variable/newsreader";
import "./customer.css";
import "./inventory.css";
import "./sales-orders.css";
import "./theme.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  description: "Auditable distribution operations across web and mobile.",
  title: "TradeFlow ERP",
};

export const viewport: Viewport = {
  initialScale: 1,
  viewportFit: "cover",
  width: "device-width",
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
