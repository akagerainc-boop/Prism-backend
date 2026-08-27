# Prism Scanner backend

The Python/MySQL server behind the Prism Scanner Flutter app: email+OTP login, Prism
Cloud document storage (documents stored as BLOBs in MySQL, not on local disk —
Render's filesystem is ephemeral), passport-photo background replacement, and
document reconstruction for **Perfect OCR**.

**No OCR/layout ML model runs on this backend.** Recognition happens client-side —
the Flutter app reads each scanned page with Gemini 3.6 Flash (multimodal, via the
Firebase AI SDK already used for document classification) and sends back a
structured description of the page (headings, paragraphs, tables, formulas as
LaTeX, figures, each with its position). This backend's job is only to turn that
structure into a real, position-preserving PDF — see `app/routers/perfect.py`'s
docstring for the full architecture rationale (this was a deliberate choice to
keep the backend light enough for Render's free tier; heavier self-hosted OCR
models like PaddleOCR/TrOCR were considered and rejected for that reason).

Every endpoint here matches an exact contract already written into the Flutter
client — see the header comment at the top of each file in `app/routers/` for the
Dart file it corresponds to.

## What's real vs. what needs your input

**Fully implemented, ready to run as soon as you configure it:**
- Email + OTP login (`/auth/email/*`) — real OTP generation, hashing, rate limiting,
  and email delivery (Resend on Render — Gmail SMTP is blocked outbound there —
  or Gmail SMTP for local dev).
- Prism Cloud (`/cloud/*`) — account creation, storage-quota enforcement, document
  upload/list/download, backed by real MySQL rows (including the file bytes
  themselves — see `documents.file_data`).
- Wallet card sync (`/cloud/cards`) — bank/ID/passport/license cards, including
  the CVV, upsert/list/delete across devices. Not counted against the document
  storage quota (a different, much smaller data domain). See "Known
  limitations" below — the same weak account auth that applies to `/cloud/documents`
  applies here too, and matters more given what's stored.
- Passport-photo background replacement (`/passport-photo/process`) — real subject
  segmentation (`rembg`) composited onto solid white, with quality guardrails.
- **Perfect OCR** (`/document/perfect/page`, `/document/perfect/book`) — takes the
  structure Gemini already recognized client-side and reconstructs a real PDF via
  `export_clean_pdf` (headings/paragraphs at their real position and size, real
  editable-looking tables with correct cells, LaTeX formulas rendered as actual
  typeset math via matplotlib's mathtext, figures/diagrams/charts embedded as
  cropped images at their detected region). Continuous/multi-page scans reuse the
  same merged-PDF job mechanism as `/document/book/structure` below.
- The two original OCR endpoints (`/document/ocr`, `/document/ocr/book`) — their
  response shape is frozen exactly as the client already expects, but **these are
  stubs**: they scan/crop the image with OpenCV and always return empty text. No
  text-recognition engine runs here; use Perfect OCR (above) for real recognition.
- `/document/structure`, `/document/book/structure`, `/document/{format}/export` —
  the request handling, JSON document model, and PDF/DOCX/Markdown/XLSX export
  writers are real and exercised (see Perfect OCR above, which is built on this
  same reconstruction layer), but these specific endpoints still call the layout
  parser with an **empty payload** (`normalize_page({}, ...)` in
  `app/routers/structure.py`) — they produce a scanned page with no recognized
  elements. They're a leftover scaffold from an earlier, abandoned attempt to wire
  in a self-hosted PaddleOCR pipeline (there's an orphaned `.venv-ocr`-style local
  install on the dev machine that was never committed); Perfect OCR is the real,
  working replacement for that idea.

**Needs your input before it actually works end to end:**
1. A MySQL database reachable via `MYSQL_*` in `.env` (XAMPP locally, FreeDB or
   similar in production), with `schema.sql` imported.
2. Email delivery configured — see `.env.example`'s `EMAIL_PROVIDER` /
   `RESEND_API_KEY` (Render) or `SMTP_*` (local), or `SMTP_DEV_MODE=true` to log
   codes instead while testing.
3. A `JWT_SECRET` value (one command, see below).
4. `rembg`/`onnxruntime` (passport photo) download a model file on first use —
   needs network access the first time.
5. Perfect OCR needs nothing extra on this backend (just `pip install -r
   requirements.txt`, which now includes `matplotlib` for formula rendering) —
   the Gemini calls happen entirely in the Flutter app via Firebase AI.

## Quick start

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`:
- Leave `MYSQL_*` as-is if XAMPP's MySQL is default (root, no password, port 3306).
- Generate a `JWT_SECRET`:
  ```powershell
  python -c "import secrets; print(secrets.token_urlsafe(48))"
  ```
- For a first test run without email set up yet: `SMTP_DEV_MODE=true` (the OTP is
  logged to the console instead of sent). Switch it to `false` once you've added
  the App Password below.

Start XAMPP → Control Panel → start **MySQL** → open **phpMyAdmin**
(`http://localhost/phpmyadmin`) → **Import** tab → choose `backend/schema.sql` →
Go. (Or from a terminal: `mysql -u root -p < schema.sql`.)

Run the server:

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` matters: the Flutter client's default `PRISM_API_BASE_URL` is
`http://10.0.2.2:8000`, the Android emulator's alias for the host machine, which
only reaches a server bound to all interfaces, not just `127.0.0.1`.

Check it's alive:
- `http://localhost:8000/health` → `{"status": "ok"}`
- `http://localhost:8000/health/detail` → MySQL/SMTP/JWT/OCR status at a glance —
  the fastest way to see *why* something isn't working during setup.
- `http://localhost:8000/docs` → interactive API docs for every endpoint.

## Getting a Gmail App Password

Regular Gmail passwords don't work for SMTP once 2-Step Verification is on
(and Google requires 2FA for this anyway). Steps for `akagerainc@gmail.com`:

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security).
2. Turn on **2-Step Verification** if it isn't already.
3. Go to **App passwords** (search for it if it's not on the main security page).
4. Create one named e.g. "Prism Scanner backend" — Google shows a 16-character
   password like `abcd efgh ijkl mnop`.
5. Paste it into `.env` as `SMTP_APP_PASSWORD` **without spaces**.
6. Set `SMTP_DEV_MODE=false`.

## Endpoint contracts

Every router file documents its own contract in its module docstring. Summary:

| Method | Path | Client file |
|---|---|---|
| POST | `/auth/email/request-otp` | `auth_service.dart` |
| POST | `/auth/email/verify-otp` | `auth_service.dart` |
| POST | `/cloud/account` | `prism_cloud_service.dart` |
| GET | `/cloud/documents` | `prism_cloud_service.dart` |
| POST | `/cloud/documents` | `prism_cloud_service.dart` |
| GET | `/cloud/documents/{id}/file` | **new** — see below |
| POST | `/passport-photo/process` | `passport_photo_service.dart` |
| POST | `/document/ocr` | `cloud_ocr_service.dart` (frozen shape; stub, always empty text) |
| POST | `/document/ocr/book` | `cloud_ocr_service.dart` (frozen shape; stub, always empty text) |
| POST | `/document/structure` | `cloud_ocr_service.dart` (`buildStructuredScan`; parses with an empty payload — no recognized elements yet) |
| POST | `/document/book/structure` | `cloud_ocr_service.dart` (`buildBook`; same caveat) |
| GET | `/document/book/{jobId}/file` | shared by `/document/book/structure` and `/document/perfect/book` |
| POST | `/document/{format}/export` | not yet wired client-side |
| POST | `/document/perfect/page` | `perfect_ocr_service.dart` (`processSinglePage`) — real, working |
| POST | `/document/perfect/book` | `perfect_ocr_service.dart` (`processBook`) — real, working |
| GET/POST | `/cloud/cards` | `wallet_cloud_service.dart` — Wallet card sync, real, working |
| GET | `/cloud/cards/{id}/front`, `/back` | `wallet_cloud_service.dart` |
| DELETE | `/cloud/cards/{id}` | `wallet_cloud_service.dart` |

**`GET /cloud/documents/{id}/file` is new** — `PrismCloudService.downloadDocument()`
was added to the Flutter client to call it (cross-device document download), so
this one's already wired up on both sides.

**Perfect OCR is the real, working document-understanding feature** — the "OCR"
quick action on the home screen. `/document/structure` and `/document/book/structure`
are an older, still-present scaffold that was meant to run a self-hosted layout/OCR
model server-side; that integration was never finished (see above), so they parse
an empty payload today. Don't build new client features against them — use
`/document/perfect/*` instead.

## Project layout

```
backend/
  schema.sql              MySQL DDL — import this, not the ORM, to create the DB
  requirements.txt        Everything the backend needs (no OCR/layout ML model —
                           recognition runs client-side via Gemini; see above)
  .env.example            Copy to .env and fill in
  app/
    main.py                FastAPI app, CORS, error envelope, /health
    config.py               All settings, loaded from .env
    db.py                    SQLAlchemy engine/session
    models.py                ORM models (mirrors schema.sql)
    schemas.py                Pydantic request/response models
    security.py                OTP hashing, JWT, email validation
    mailer.py                   Resend/Gmail SMTP email sending
    plans.py                     Free/Student/Personal storage limits
    storage.py                    Path helpers, path-traversal guards
    passport.py                    rembg background replacement
    imaging.py                      Shared image preprocessing helpers
    ocr_support.py                   Upload handling shared by OCR/structure/perfect
    document_model.py                 Structured JSON document model + builders
    book_pdf.py                        Merged, page-numbered PDF (continuous mode)
    exporters/                          pdf / docx / markdown / xlsx writers
                                         (pdf_export.export_clean_pdf is Perfect
                                         OCR's real, position-preserving reconstruction)
    routers/
      auth.py       cloud.py         passport_photo.py
      ocr.py         structure.py    perfect.py   (Perfect OCR — real, working)
```

## Known limitations, honestly

- No authentication is enforced on `/cloud/*` beyond the `X-User-Email` header
  matching a row — anyone who knows (or guesses) an email can read that account's
  document list. The OTP-issued `sessionToken` (JWT) exists but isn't yet checked
  as a bearer token on the Prism Cloud endpoints. Add that before this handles
  real user data outside your own testing. **This applies to `/cloud/cards` too,
  and matters more there** — full card numbers and CVVs, not just document
  metadata. Wiring real bearer-token auth onto `/cloud/*` should happen before
  Wallet sync is used with a real card, not just a test one.
- `/document/{format}/export` accepts an arbitrary structured-document JSON body
  with no size cap beyond FastAPI's defaults — fine for testing, worth a limit
  before this is internet-facing.
- Perfect OCR's recognition quality is entirely Gemini's — this backend just
  reconstructs whatever structure it returns. Rendered formulas rely on
  matplotlib's mathtext, which covers most common LaTeX (fractions, roots,
  sub/superscripts, Greek letters, sums, integrals) but not full LaTeX (e.g.
  `\begin{...}` environments); anything mathtext can't parse falls back to
  drawing the raw LaTeX source as plain text rather than silently dropping it.
- `/document/structure`, `/document/book/structure`, and `/document/ocr*` are a
  leftover scaffold (see "What's real vs. what needs your input" above) — safe
  to delete once nothing references them, or to finish wiring up a self-hosted
  engine later if Perfect OCR's Gemini dependency ever needs an offline
  alternative.
