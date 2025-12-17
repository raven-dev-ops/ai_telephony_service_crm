# Raven CRM Frontend (TypeScript)

Vite + React + TypeScript frontend for the AI Telephony Service & CRM backend.

## Run locally

1) Start the backend (separately):

```bash
cd backend
uvicorn app.main:app --reload
```

2) Start the frontend:

```bash
cd frontend
npm install
npm run dev
```

## API access

In local dev you can leave “API Base URL” blank to use the Vite dev proxy (see `frontend/vite.config.ts`).
For hosted environments, set “API Base URL” to your backend URL (e.g. `https://api.example.com`).

Auth headers are stored in localStorage via the Settings panel:

- Owner: `X-API-Key` + `X-Owner-Token`
- Admin: `X-Admin-API-Key`
