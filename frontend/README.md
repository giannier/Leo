# Leo — Frontend

Next.js 15 (App Router) + React + Tailwind CSS scaffold for the Leo tenant/admin
panel. This is TASK-001 scaffolding only — no auth pages, wizard, or API client
yet (those land in TASK-020+).

## Requirements

- Node.js >= 20 (developed against v24)
- npm

## Getting started

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Scripts

- `npm run dev` — start the dev server (Turbopack)
- `npm run build` — production build (type-checks + lints via Next)
- `npm run start` — serve a production build
- `npm run lint` — ESLint

## Docker

Built and run as the `frontend` service in
`../deploy/docker-compose.dev.yml` (see `Dockerfile` in this directory).
