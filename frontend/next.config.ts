import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  reactCompiler: true,
  // Self-contained server bundle (.next/standalone) for the Docker image.
  output: "standalone",
  // TEMPORARY — allow LAN + cloudflared tunnel phone testing. Remove when done.
  allowedDevOrigins: ["192.168.8.125", "*.trycloudflare.com"],
  turbopack: {
    // Pin Turbopack's root to the frontend directory.
    // Without this Next.js walks up from frontend/ looking for lockfiles and
    // finds the stub package-lock.json at the repo root, treating the whole
    // repo as the workspace root — causing OOM crashes on Windows.
    root: path.resolve(__dirname),
  },
};

export default nextConfig;
