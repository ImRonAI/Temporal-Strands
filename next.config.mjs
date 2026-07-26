import { fileURLToPath } from "node:url"
import { dirname } from "node:path"

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Pin the workspace root. Without this Next walks up looking for a lockfile,
  // finds /Users/tims-stuff/package-lock.json, and treats the HOME directory as
  // the project root — which changes how it resolves files and watches for
  // changes. Anchoring to this file's own directory keeps it inside the project.
  turbopack: {
    root: dirname(fileURLToPath(import.meta.url)),
  },
  // Next 16 blocks cross-origin requests to dev resources (/_next/*) by
  // default. Opening the app on a LAN address instead of localhost therefore
  // serves the page HTML but blocks the client bundle and HMR — React never
  // hydrates, so the composer renders and does nothing when you hit send.
  // Allowing the private ranges restores dev access from another device on
  // the network (phone, tablet, second machine). Dev-only setting; it has no
  // effect on a production build.
  allowedDevOrigins: ["192.168.1.197", "127.0.0.1", "localhost"],
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
}

export default nextConfig
