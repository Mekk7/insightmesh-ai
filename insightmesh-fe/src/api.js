// Single source of truth for the backend origin.
//
// In production (e.g. Vercel) set VITE_API_URL to the Railway backend ORIGIN
// with NO trailing slash and NO /api suffix, e.g.:
//   VITE_API_URL=https://insightmesh-ai-production.up.railway.app
//
// Locally it's empty, so requests stay relative ("/api") and go through
// Vite's dev proxy (vite.config.js) to localhost:8000.
//
// Callers build full paths as `${API_BASE}/api/...`. The axios baseURL and
// the SSE fetch() base are both derived from this.
const API_BASE = import.meta.env.VITE_API_URL || '';
export default API_BASE;
