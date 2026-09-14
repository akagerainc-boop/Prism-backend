# Docs Scanner admin dashboard

React (Vite + TypeScript + Tailwind) SPA for managing Docs Scanner: users, Docs
Cloud usage per account, installed devices, force-update version control, and
sending push notifications. Talks to `../app/routers/admin.py` in the backend
right next to it — see the backend's own `README.md` ("Admin dashboard" and
"Deploying to Railway" sections) for the full setup, including how to create the
first admin login (`python seed_admin.py ...` — there's no public signup).

## Local dev

```powershell
copy .env.example .env.local
REM edit .env.local: VITE_API_BASE_URL -> your local backend, e.g. http://localhost:8000
npm install
npm run dev
```

## Build

```powershell
npm run build
```

Outputs a static `dist/` — `npm run start` serves it via `serve -s dist -l $PORT`
(what Railway runs in production; see `railway.json`).

## Structure

```
src/
  lib/
    api.ts      fetch wrapper -- attaches the admin JWT, VITE_API_BASE_URL
    auth.tsx     AuthProvider/useAuth -- login/logout, token in localStorage
    types.ts      Mirrors backend/app/schemas.py's Admin* models
    format.ts      formatBytes/formatDate helpers
  components/
    IOSDialog.tsx   Shared iPhone-style confirm/detail modal (same look as the
                     Flutter app's PrismIOSDialog) -- every destructive or
                     important action goes through this, not a browser confirm()
    Layout.tsx        Sidebar nav + light/dark theme toggle + logout
    Toast.tsx          Non-blocking success/error/info feedback
  pages/
    LoginPage.tsx       UsersPage.tsx        DevicesPage.tsx
    ForceUpdatePage.tsx  NotificationsPage.tsx
```
