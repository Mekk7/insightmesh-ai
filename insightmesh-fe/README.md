# InsightMesh AI — Frontend

React 19 + Vite 7 + Tailwind 3 dashboard for the InsightMesh AI backend.

## Development

```bash
npm install
npm run dev
# → http://127.0.0.1:5173
```

Vite proxies `/api/*` to `http://127.0.0.1:8000` (the FastAPI backend). Configure
in `vite.config.js`.

## Scripts

| Command | What |
|---|---|
| `npm run dev` | Hot-reloading dev server |
| `npm run build` | Production bundle into `dist/` |
| `npm run preview` | Serve the built bundle locally |
| `npm run lint` | ESLint over the project |
| `npm test` | Run Vitest tests (once configured) |

## Environment variables

Build-time variables (prefixed with `VITE_`):

| Var | Default | Notes |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` | Where the dashboard sends requests. Use `/api` to keep things behind the Vite proxy. |
| `VITE_API_TIMEOUT` | `0` (no timeout) | Axios timeout in ms |

Set them in a `.env.local` (gitignored) or pass at build time:

```bash
VITE_API_BASE_URL=https://api.yourdomain.com npm run build
```

## Structure

```
src/
├── App.jsx                  ← main dashboard (5 tabs)
├── main.jsx                 ← React mount
├── index.css                ← Tailwind base + dark theme
├── App.css                  ← minimal global tweaks
├── assets/                  ← static assets
└── components/
    └── InsightsDashboard.jsx ← alternate dashboard layout (not currently mounted)
```

## Notes for contributors

- We use Tailwind utility classes — keep CSS files minimal.
- Dark theme is the default; the body background is `bg-zinc-950` and text is `text-zinc-200`.
- Recharts for all charts.
- LocalStorage key for UI preferences: `insightmesh_ui_v1`.
- Cmd/Ctrl+Enter from inside any input runs the active tab's pipeline.
