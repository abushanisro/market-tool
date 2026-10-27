/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async redirects() {
    return [{ source: "/", destination: "/docs/general/introduction", permanent: false },
            { source: "/docs", destination: "/docs/general/introduction", permanent: false }];
  },
};
export default nextConfig;
