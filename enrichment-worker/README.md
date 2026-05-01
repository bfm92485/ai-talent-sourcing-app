# Enrichment Worker v2.0

FastAPI service that processes inbound email webhooks and creates AI-enriched Job Applicant records in ERPNext.

## Architecture

```
Primitive (managed SaaS)  →  Webhook POST (HMAC-signed)  →  This Worker  →  ERPNext REST API
     ↓                                                            ↓
  tar.gz archive                                          BAML ExtractResume
  (attachments)                                           (Gemini Flash)
```

**Key design decisions:**
- **Primitive** is a managed SaaS (primitive.dev) — no self-hosted VPS/Postfix needed
- Webhook returns **200 immediately**, processes async in background (prevents retry storms)
- **Idempotency** via event_id/Message-ID dedup (Primitive retries up to 6 times)
- `reference_name` uses ERPNext's **auto-generated name** (e.g., `HR-APP-2026-00042`), NOT email
- Attachments uploaded as **private files** (`is_private=1`) — resumes contain PII
- PDF extraction uses **pdfplumber** (MIT license), not PyMuPDF (AGPL)

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Health check for Railway |
| POST | `/webhook/inbound-email` | HMAC-SHA256 | Production webhook from Primitive |
| POST | `/webhook/test` | None | Dev/test endpoint (disable in prod) |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `WEBHOOK_SECRET` | Yes (prod) | HMAC secret from Primitive dashboard |
| `ERPNEXT_URL` | Yes | ERPNext instance URL |
| `ERPNEXT_API_KEY` | Yes | ERPNext API key |
| `ERPNEXT_API_SECRET` | Yes | ERPNext API secret |
| `GEMINI_API_KEY` | Yes | Google Gemini API key for BAML |
| `OPENAI_API_KEY` | Fallback | OpenAI-compatible API key |
| `MAX_ATTACHMENT_SIZE` | No | Max attachment size in bytes (default: 25MB) |
| `DATA_TTL_DAYS` | No | Days until data expires (default: 90) |
| `PORT` | No | Server port (default: 8080) |
| `DISABLE_TEST_ENDPOINT` | No | Set to "true" in production |

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Generate BAML client
cd baml_src && baml-cli generate && cd ..

# Set environment variables
export ERPNEXT_URL="https://erpnext-v16-talent-sourcing-production.up.railway.app"
export ERPNEXT_API_KEY="your-key"
export ERPNEXT_API_SECRET="your-secret"
export GEMINI_API_KEY="your-gemini-key"

# Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8090 --reload
```

## Testing

```bash
# Test with a resume
curl -X POST http://localhost:8090/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"resume_text": "John Doe\njohn@example.com\nSenior Engineer at Acme Corp..."}'
```

## Deployment (Railway)

The service deploys as a separate Railway service in the same project as ERPNext.
See `railway.toml` and `Dockerfile` for configuration.

## Bug Fixes (v2.0 — Council Review 2026-05-01)

- **reference_name**: Now uses auto-generated `name` from create response, not email
- **Idempotency**: Dedup on event_id/Message-ID prevents duplicate processing
- **is_private**: All file uploads default to private (PII protection)
- **PyMuPDF → pdfplumber**: MIT license, no AGPL source-disclosure obligations
- **Async processing**: Returns 200 immediately, processes in background
- **Primitive SaaS**: Corrected architecture — no VPS needed, attachments via tar.gz
- **Dedup by email filter**: Uses `find_job_applicant_by_email()` not name lookup
