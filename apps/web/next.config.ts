import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  poweredByHeader: false,
  transpilePackages: [
    "@tradeflow/api-client",
    "@tradeflow/customer-directory",
    "@tradeflow/platform-session",
    "@tradeflow/telemetry",
  ],
};

export default nextConfig;
