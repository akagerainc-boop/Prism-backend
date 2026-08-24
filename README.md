# Prism Scanner backend

The Python/MySQL server behind the Prism Scanner Flutter app: email+OTP login, Prism
Cloud document storage, passport-photo background replacement, and a PaddleOCR 3.x
document-understanding pipeline. There is a single OCR/document-scanning pipeline —
PP-StructureV3 running PP-OCRv5 — used for both plain-text OCR and full structured
document parsing. PaddleOCR-VL runs alongside it by default on every
`/document/structure` and `/document/book/structure` request as an independent
cross-check (see "PaddleOCR-VL cross-check" below) — it's a second full model
pass, so it can be turned off with `ENABLE_PADDLE_VL=false` on a
resource-constrained deployment, falling back to PP-StructureV3 alone.

Every endpoint here matches an exact contract already written into the Flutter
client — see the header comment at the top of each file in `app/routers/` for the
Dart file it corresponds to. Nothing on the client needs to change to talk to this
server except the one deliberately new endpoint noted below.

## What's real vs. what needs your input

**Fully implemented, ready to run as soon as you configure it:**
- Email + OTP login (`/auth/email/*`) — real OTP generation, hashing, rate limiting,
  and Gmail delivery.
- Prism Cloud (`/cloud/*`) — account creation, storage-quota enforcement, document
  upload/list/download, all backed by real MySQL rows and files on disk.
- Passport-photo background replacement (`/passport-photo/process`) — real subject
  segmentation (`rembg`) composited onto solid white, with quality guardrails.
- The two original OCR endpoints (`/document/ocr`, `/document/ocr/book`) — their
  response shape is frozen exactly as the client already expects.
- The full structured-document pipeline (`/document/structure`,
  `/document/book/structure`, `/document/book/{id}/file`,
  `/document/{format}/export`) — real PaddleOCR 3.x integration, not a stub,
  with PaddleOCR-VL cross-checking every element by default (see below).

**Needs your input before it actually works end to end:**
1. XAMPP's MySQL service running, and `schema.sql` imported.
2. A Gmail **App Password** for `akagerainc@gmail.com` (see below) — or set
   `SMTP_DEV_MODE=true` to log codes instead of emailing them while you test
   everything else.
3. A `JWT_SECRET` value (one command, see below).
4. If you want the OCR endpoints working: a **separate Python 3.12/3.13
   environment** for `requirements-ocr.txt` — see "Two-venv setup" below.
   Everything else (auth, Prism Cloud, passport photo) runs fine on Python 3.14
   without it.
5. `rembg`/`onnxruntime` (passport photo) download a model file on first use —
   needs network access the first time.

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

## Two-venv setup (only needed for the OCR endpoints)

`paddlepaddle` — the engine PaddleOCR runs on — currently ships wheels for
Python 3.9 through 3.13 only (verified against PyPI's `paddlepaddle` 3.3.1
release; check `requirements-ocr.txt`'s header comment if that's since
changed), and **no wheel for 3.14**. Since this machine's default Python is
3.14.7, the OCR pipeline needs its own environment on whichever 3.9–3.13
you have installed (this machine already has 3.9 — `py -0p` to check yours):

```powershell
py -3.9 -m venv .venv-ocr
.venv-ocr\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-ocr.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run *this* server (not the 3.14 one) when you need `/document/structure`,
`/document/book/structure`, or `/document/{format}/export` to actually work.
The other endpoints (auth, Prism Cloud, passport photo, and even the two
simple `/document/ocr*` endpoints — they also need PaddleOCR, so they need this
venv too) behave identically in either environment; only whether `paddleocr`
itself is importable differs. `GET /health/detail` tells you which pipelines are
loaded and on which device (CPU/GPU) at a glance.

If you have an NVIDIA GPU: install `paddlepaddle-gpu` instead of `paddlepaddle`
per the header of `requirements-ocr.txt`, and the backend auto-detects and uses
it (`PADDLE_DEVICE=auto`, the default) — no code change needed.

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
| POST | `/document/ocr` | `cloud_ocr_service.dart` (frozen shape) |
| POST | `/document/ocr/book` | `cloud_ocr_service.dart` (frozen shape) |
| POST | `/document/structure` | not yet wired client-side |
| POST | `/document/book/structure` | not yet wired client-side |
| GET | `/document/book/{jobId}/file` | not yet wired client-side |
| POST | `/document/{format}/export` | not yet wired client-side |

**`GET /cloud/documents/{id}/file` is new** — `PrismCloudService.downloadDocument()`
was added to the Flutter client to call it (cross-device document download), so
this one's already wired up on both sides.

**The structured-document endpoints are not yet called from Flutter.** They exist
and work; the app's continuous-scan-mode and PDF-tools screens still build books
and exports on-device. Wiring continuous mode to call `/document/book/structure`
(instead of, or in addition to, the on-device PDF assembly) is the next step to
actually use this pipeline from the app — see the design note in
`app/routers/structure.py` for the exact response shape (`jobId`, `pageCount`,
`bookPdfUrl`, `document`, `pages`) to build the client method against.

## PaddleOCR-VL cross-check

`/document/structure` and `/document/book/structure` run PaddleOCR-VL alongside
PP-StructureV3 by default (`ENABLE_PADDLE_VL=true`). PP-StructureV3 stays the
source of truth for structure — bbox, tables, reading order — and VL is a second,
independently-trained model reading the same page. Where they agree, that's real
corroborating evidence; where they don't, both readings are kept so a human (or
the client UI) can see the disagreement instead of one engine silently
overwriting the other. Concretely, on every `DocElement`:

- `crossValidated` — `true` if VL's reading of that region matched closely
  (fuzzy string similarity ≥ 0.6), `false` if it disagreed, `null` if VL didn't
  run (disabled, not installed, or its pass failed — PP-StructureV3's own
  result is still returned complete either way, see `PipelineFailed` handling).
- `vlAgreement` — the raw similarity ratio (0–1) behind that verdict.
- `vlText` — only set when `crossValidated` is `false`: VL's own reading of
  that region, as an alternate for review. Never used to overwrite `text`.
- `confidence` — nudged **halfway toward, never at, 1.0** when `crossValidated`
  is `true`. Two independent models agreeing is evidence, not proof — this
  project does not claim 100% accuracy anywhere, by design.

`DocPage.vlAvailable` / `DocPage.vlText` carry VL's whole-page reading, and
`StructuredDocument.vlEngine` names the exact VL pipeline version used
(`null` when VL didn't run for that document). `/document/ocr` and
`/document/ocr/book` deliberately do **not** run VL — their frozen `{"text": ...}`
response shape has no field to carry any of the above, so the extra model pass
would only add latency with no visible benefit; use the `/document/structure`
routes when you want the cross-checked result.

Cost: this is a second full model load and a second `predict()` call on every
structure request, so expect roughly double the latency and memory versus
PP-StructureV3 alone. Set `ENABLE_PADDLE_VL=false` in `.env` to disable it.

## Project layout

```
backend/
  schema.sql              MySQL DDL — import this, not the ORM, to create the DB
  requirements.txt        Everything except PaddleOCR (works on Python 3.14)
  requirements-ocr.txt    paddlepaddle + paddleocr (needs Python 3.12/3.13)
  .env.example            Copy to .env and fill in
  app/
    main.py                FastAPI app, CORS, error envelope, /health
    config.py               All settings, loaded from .env
    db.py                    SQLAlchemy engine/session
    models.py                ORM models (mirrors schema.sql)
    schemas.py                Pydantic request/response models
    security.py                OTP hashing, JWT, email validation
    mailer.py                   Gmail SMTP sending
    plans.py                     Free/Student/Personal storage limits
    storage.py                    Path helpers, path-traversal guards
    passport.py                    rembg background replacement
    imaging.py                      Shared image preprocessing helpers
    ocr_support.py                   Upload handling shared by OCR/structure
    paddle_pipeline.py                PaddleOCR 3.x integration (isolated on purpose)
    document_model.py                  Structured JSON document model + builders
    book_pdf.py                         Merged, page-numbered PDF (continuous mode)
    exporters/                          pdf / docx / markdown / xlsx writers
    routers/
      auth.py         cloud.py       passport_photo.py
      ocr.py          structure.py
```

## Known limitations, honestly

- No authentication is enforced on `/cloud/*` beyond the `X-User-Email` header
  matching a row — anyone who knows (or guesses) an email can read that account's
  document list. The OTP-issued `sessionToken` (JWT) exists but isn't yet checked
  as a bearer token on the Prism Cloud endpoints. Add that before this handles
  real user data outside your own testing.
- `/document/{format}/export` accepts an arbitrary structured-document JSON body
  with no size cap beyond FastAPI's defaults — fine for testing, worth a limit
  before this is internet-facing.
- The structured-document endpoints haven't been exercised against a real
  multi-column/table-heavy scan yet (this sandbox has no way to run the actual
  PaddleOCR model). The plumbing (request handling, JSON model, PDF/DOCX/XLSX
  export) is real and compiles/imports cleanly; the model *output quality* on a
  real page is unverified until you run it against one.
