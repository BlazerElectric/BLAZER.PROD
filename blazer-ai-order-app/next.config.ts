import type { NextConfig } from "next";

const frameAncestors = process.env.FRAME_ANCESTORS || "'self' https://buyblazer.com https://*.buyblazer.com";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // The catalog JSON is read with fs at runtime, so tracing can't discover it on its own.
  outputFileTracingIncludes: {
    "/**/*": ["./data/*.json", "./config/*.csv"],
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // Allow embedding only inside the Billtrust storefront.
          { key: "Content-Security-Policy", value: `frame-ancestors ${frameAncestors}` },
          { key: "X-Content-Type-Options", value: "nosniff" },
          // Keep the origin in the referrer so the iframe can verify its parent page.
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
      {
        source: "/billtrust-bridge.js",
        headers: [
          { key: "Cache-Control", value: "public, max-age=300, stale-while-revalidate=3600" },
          { key: "Access-Control-Allow-Origin", value: "*" },
        ],
      },
    ];
  },
};

export default nextConfig;
