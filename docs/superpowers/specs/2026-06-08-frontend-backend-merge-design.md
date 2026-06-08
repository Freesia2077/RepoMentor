# Frontend-Backend Merge Design

> **Topic:** Merging the React frontend and Fastify backend into a single-port deployment model.
> **Date:** 2026-06-08

## 1. Architecture Overview

We are moving to a **Hybrid Deployment Model**. The Node.js (Fastify) backend will act as the single entry point for both API requests and static asset delivery.
- Frontend assets (HTML, CSS, JS) built via Vite will be served from `web/dist`.
- Backend endpoints will be isolated under an `/api/*` prefix.
- The React application retains SPA routing, with Fastify serving `index.html` as a fallback for non-API requests.

## 2. API Routing Strategy (Backend)

To prevent URL collisions between frontend SPA routes (like `/:taskId/report`) and backend REST routes:

- **Route Prefixing:** All routes registered in `src/routes/analysis.ts` and `src/routes/stream.ts` will be mounted with `{ prefix: '/api' }` in `src/index.ts`.
- **Static File Serving:** Install `@fastify/static@^8.x` (Fastify 5 compatible).
  ```typescript
  if (process.env.NODE_ENV === 'production') {
    await app.register(fastifyStatic, {
      root: path.resolve(process.cwd(), 'web/dist'),
      wildcard: false, // Prevent conflict with SPA fallback
    });
  }
  ```
- **SPA Fallback:** Use a custom 404 handler to distinguish between missing API endpoints and SPA navigation requests.
  ```typescript
  if (process.env.NODE_ENV === 'production') {
    app.setNotFoundHandler((request, reply) => {
      if (request.url.startsWith('/api')) {
        reply.status(404).send({ error: 'not_found' });
      } else {
        reply.sendFile('index.html');
      }
    });
  }
  ```
- **Environment Definition:** Ensure `.env.example` includes `NODE_ENV=development` so local executions don't crash evaluating `process.env.NODE_ENV`.

## 3. Client Migration (Frontend)

- **Vite Proxy:** Configure `web/vite.config.ts` to proxy `/api` requests to `http://127.0.0.1:3000` during local development. This allows us to deprecate and delete `mock-server.mjs`.
- **Endpoint Upgrades:** Globally search the `web/src` codebase for `/analysis` to ensure complete coverage. Known endpoints to update to `/api/analysis`:
  1. `POST /analysis` (Create task)
  2. `GET /analysis/:id` (Poll status/result)
  3. `POST /analysis/:id/ask` (Interaction response)
  4. `EventSource` stream connection (`/api/analysis/:id/stream`)

## 4. Build Orchestration

To ensure `web/dist` exists before the backend boots in production, we will establish a unified monorepo build pipeline in the root `package.json`.

```json
  "workspaces": ["web"],
  "scripts": {
    "dev:backend": "tsx watch src/index.ts",
    "dev:frontend": "npm run dev --workspace=web",
    "dev": "concurrently \"npm run dev:backend\" \"npm run dev:frontend\"",
    "build:backend": "tsc",
    "build:frontend": "npm run build --workspace=web",
    "build": "npm run build:frontend && npm run build:backend",
    "start": "NODE_ENV=production node dist/index.js",
    "test": "vitest run"
  }
```
*(Requires adding `"workspaces": ["web"]` to the root `package.json`, and ensuring `concurrently` is installed).*

## 5. Documentation (CLAUDE.md)

Add a brief monorepo structural overview to `CLAUDE.md` to clarify the separation of concerns:
- `/src`: Fastify Backend & AI Orchestration logic.
- `/web`: React + Vite Frontend application.
- `/docs`: Superpowers plans and design documents. (Ensure this directory exists, e.g., via `.gitkeep`).
