# Enrichment Worker

FastAPI service that receives inbound email webhooks from PrimitiveMail, parses resume attachments using BAML AI extraction (Gemini Flash), and creates fully-enriched Job Applicant records in ERPNext v16.

## Architecture

```
PrimitiveMail (SMTP) → Webhook POST → This Worker → ERPNext REST API
```

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | None | Health check |
| POST | `/webhook/inbound-email` | HMAC-SHA256 | Production webhook receiver |
| POST | `/webhook/test` | None | Development testing (no HMAC) |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WEBHOOK_SECRET` | Yes | `dev-secret-change-me` | Shared HMAC secret with PrimitiveMail |
| `ERPNEXT_URL` | Yes | (Railway URL) | ERPNext base URL |
| `ERPNEXT_API_KEY` | Yes | — | ERPNext API key |
| `ERPNEXT_API_SECRET` | Yes | — | ERPNext API secret |
| `PRIMITIVEMAIL_BASE_URL` | Yes | `http://localhost:3000` | PrimitiveMail attachment download base |
| `GEMINI_API_KEY` | Yes | — | Google Gemini API key for BAML |
| `MAX_ATTACHMENT_SIZE` | No | `25000000` | Max attachment size in bytes |
| `DATA_TTL_DAYS` | No | `90` | Days until data expiry |
| `PORT` | No | `8080` | Server port |

## Local Development

```bash
cd enrichment-worker
pip install -r requirements.txt

# Generate BAML client
cd .. && baml-cli generate && cd enrichment-worker

# Set environment variables
export ERPNEXT_URL=https://erpnext-v16-talent-sourcing-production.up.railway.app
export ERPNEXT_API_KEY=your_key
export ERPNEXT_API_SECRET=your_secret
export GEMINI_API_KEY=your_gemini_key
export WEBHOOK_SECRET=your_shared_secret

# Run
uvicorn app.main:app --reload --port 8080
```

## Testing

```bash
# Health check
curl http://localhost:8080/health

# Test with resume text (no HMAC required)
curl -X POST http://localhost:8080/webhook/test \
  -H "Content-Type: application/json" \
  -d '{"resume_text": "John Doe\njohn@example.com\nSenior Engineer at Acme Corp\nSkills: Python, FastAPI, Docker"}'

# Simulate PrimitiveMail webhook (with HMAC)
PAYLOAD='{"from":"John Doe <john@example.com>","to":["apply@talent.amdg.cc"],"subject":"Application","body_text":"...","attachments":[]}'
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | cut -d' ' -f2)
curl -X POST http://localhost:8080/webhook/inbound-email \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Signature: $SIGNATURE" \
  -d "$PAYLOAD"
```

## Deployment (Railway)

This service is designed to run as a separate service in the same Railway project as ERPNext v16. Deploy via:

```bash
railway link -p erpnext-v16-talent-sourcing
railway up --service enrichment-worker
```
