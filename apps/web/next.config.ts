import type { NextConfig } from "next";

// CSP is set dynamically in middleware.ts with nonce-based script-src.
// Only Permissions-Policy is set here (not covered by middleware).
const securityHeaders = [
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(self), geolocation=()",
  },
];

// PI-10: For production deployments, configure a CDN for static assets.
// Set `assetPrefix` to the CDN origin (e.g., CloudFront distribution URL)
// to offload static file serving from the Next.js server:
//   assetPrefix: "https://d1234567890.cloudfront.net",
// Alternatively, deploy behind a CDN reverse proxy (CloudFront, Fastly, etc.)
// that caches /_next/static/* with long Cache-Control headers.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  productionBrowserSourceMaps: false,
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "**.amazonaws.com",
      },
      {
        protocol: "https",
        hostname: "**.s3.*.amazonaws.com",
      },
      {
        protocol: "http",
        hostname: "localhost",
        port: "9000",
      },
    ],
  },
  async headers() {
    return [
      {
        // Apply security headers to all routes
        source: "/(.*)",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
