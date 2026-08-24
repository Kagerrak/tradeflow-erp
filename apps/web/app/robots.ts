import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  const baseUrl = process.env.TRADEFLOW_PUBLIC_URL ?? "http://localhost:3000";
  return {
    rules: {
      allow: ["/", "/case-study", "/demo"],
      disallow: "/api/",
      userAgent: "*",
    },
    sitemap: `${baseUrl}/sitemap.xml`,
  };
}
