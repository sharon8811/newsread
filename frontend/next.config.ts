import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Allow phones/other devices on the LAN to load dev-server assets.
  allowedDevOrigins: ["172.16.30.94"],
};

export default nextConfig;
