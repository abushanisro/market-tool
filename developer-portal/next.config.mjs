/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  skipTrailingSlashRedirect: true, // keep /dashboard/ as written: the dashboard uses relative asset paths
  async redirects() {
    return [
      { source: "/", destination: "/dashboard/", permanent: false },
      { source: "/docs", destination: "/docs/general/introduction", permanent: false },
    ];
  },
  async rewrites() {
    return [{ source: "/dashboard/", destination: "/dashboard/index.html" }];
  },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
    ] }];
  },
};
export default nextConfig;
