import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const baseUrl = process.env.TRADEFLOW_PUBLIC_URL ?? "https://tradeflow.app";
  return [
    { changeFrequency: "monthly", priority: 1, url: baseUrl },
    { changeFrequency: "monthly", priority: 0.8, url: `${baseUrl}/case-study` },
    { changeFrequency: "daily", priority: 0.9, url: `${baseUrl}/operations` },
  ];
}
