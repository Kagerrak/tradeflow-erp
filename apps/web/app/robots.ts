import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  const baseUrl = process.env.TRADEFLOW_PUBLIC_URL ?? "https://tradeflow.app";
  return {
    rules: {
      allow: ["/", "/case-study", "/operations"],
      disallow: "/api/",
      userAgent: "*",
    },
    sitemap: `${baseUrl}/sitemap.xml`,
  };
}
