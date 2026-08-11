/** UI only — all business logic lives in the Python `api` service (agent.md §16). */
const nextConfig = { reactStrictMode: true, env: { NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000/api/v1' } }
export default nextConfig
