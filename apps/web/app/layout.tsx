import "@fontsource-variable/ibm-plex-sans";
import "./globals.css";
import "./customer.css";
import "./delivery-confirmation.css";
import "./delivery-corrections.css";
import "./delivery-exceptions.css";
import "./dispatch.css";
import "./inventory.css";
import "./operations.css";
import "./payment-clearance.css";
import "./picking.css";
import "./sales-orders.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  description:
    "TradeFlow is an auditable distribution ERP that connects order approval, warehouse custody, delivery, invoicing, and payment.",
  metadataBase: new URL(
    process.env.TRADEFLOW_PUBLIC_URL ?? "http://localhost:3000",
  ),
  openGraph: {
    description:
      "Follow one accountable flow from commercial approval to warehouse custody, delivery, invoice, and payment.",
    images: [
      {
        alt: "TradeFlow operations overview",
        url: "/opengraph-image",
      },
    ],
    title: "TradeFlow ERP | Accountable distribution operations",
    type: "website",
  },
  title: {
    default: "TradeFlow ERP | Accountable distribution operations",
    template: "%s · TradeFlow ERP",
  },
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
    <html lang="en" className="font-sans" data-scroll-behavior="smooth">
      <body>{children}</body>
    </html>
  );
}
