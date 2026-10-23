// Direct local development keeps its existing URL. Docker builds use /api,
// so every screen reaches FastAPI through the same Nginx origin.
export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
).replace(/\/+$/, '')
