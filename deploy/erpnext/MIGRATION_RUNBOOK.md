# Migration Runbook: Railway → Hetzner

**Pre-requisites:**
- Hetzner VPS resized to CX32 (4 GB RAM, 80 GB disk)
- DNS A record: `erp.talent.amdg.ai` → `5.161.215.63` (in Cloudflare)
- SSH access: `ssh -i ~/.ssh/hetzner_primitivemail root@5.161.215.63`

---

## Step 0: Resize VPS (Hetzner Console)

1. Go to https://console.hetzner.cloud → Server → Resize
2. Select CX32 (2 vCPU, 4 GB RAM, 80 GB disk)
3. Confirm (~5 min downtime, preserves root disk)
4. Verify: `ssh root@5.161.215.63 "free -h"` → should show ~3.8 GB

---

## Step 1: Add Swap (Safety Net)

```bash
ssh root@5.161.215.63 << 'EOF'
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
swapon --show
free -h
EOF
```

---

## Step 2: Deploy ERPNext Stack (Empty)

```bash
ssh root@5.161.215.63 << 'EOF'
mkdir -p /opt/erpnext
cd /opt/erpnext

# Copy docker-compose.yml, Caddyfile, and .env (from this repo's deploy/erpnext/)
# Or clone the repo:
# git clone https://github.com/bfm92485/ai-talent-sourcing-app.git /tmp/ats
# cp /tmp/ats/deploy/erpnext/* /opt/erpnext/

# Create .env from example
cp example.env .env
# EDIT .env with real passwords!
nano .env

# Pull images
docker compose pull

# Start the stack
docker compose up -d

# Wait for configurator to complete
docker compose logs -f configurator
# Should see "service_completed_successfully"

# Verify all services are running
docker compose ps
EOF
```

---

## Step 3: Create Site

```bash
ssh root@5.161.215.63 << 'EOF'
cd /opt/erpnext

# Create new site
docker compose exec backend bench new-site erp.talent.amdg.ai \
  --mariadb-root-password $(grep DB_PASSWORD .env | cut -d= -f2) \
  --admin-password $(grep ADMIN_PASSWORD .env | cut -d= -f2) \
  --install-app erpnext

# Install HRMS
docker compose exec backend bench --site erp.talent.amdg.ai install-app hrms

# Verify site loads
curl -s http://localhost:8080 | head -5
EOF
```

---

## Step 4: Backup Railway

```bash
# Option A: Via Railway console (if bench is accessible)
# railway run bench --site site1.local backup --with-files

# Option B: Via ERPNext API (download backup)
ERPNEXT_URL="https://erpnext-v16-talent-sourcing-production.up.railway.app"
API_KEY="9c0d1ff01026ff2"
API_SECRET="37310956c8b05f7"

# Trigger backup
curl -X POST "$ERPNEXT_URL/api/method/frappe.utils.backups.create_backup" \
  -H "Authorization: token $API_KEY:$API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"with_files": true}'

# List backups
curl "$ERPNEXT_URL/api/method/frappe.utils.backups.fetch_latest_backups" \
  -H "Authorization: token $API_KEY:$API_SECRET"

# Download the backup files (database + private files)
# URLs will be in the response above
```

---

## Step 5: Restore to Hetzner

```bash
ssh root@5.161.215.63 << 'EOF'
cd /opt/erpnext

# Copy backup files into the container
docker compose cp ./backup.sql.gz backend:/home/frappe/frappe-bench/sites/
docker compose cp ./private-files.tar backend:/home/frappe/frappe-bench/sites/

# Restore database
docker compose exec backend bench --site erp.talent.amdg.ai restore \
  /home/frappe/frappe-bench/sites/backup.sql.gz \
  --mariadb-root-password $(grep DB_PASSWORD .env | cut -d= -f2)

# Restore private files (resumes)
docker compose exec backend bash -c "
  cd /home/frappe/frappe-bench/sites/erp.talent.amdg.ai &&
  tar -xf /home/frappe/frappe-bench/sites/private-files.tar
"

# Run migrations (in case versions differ slightly)
docker compose exec backend bench --site erp.talent.amdg.ai migrate
EOF
```

---

## Step 6: Install Custom App

```bash
ssh root@5.161.215.63 << 'EOF'
cd /opt/erpnext

# Get the custom app from GitHub
docker compose exec backend bench get-app \
  https://github.com/bfm92485/ai-talent-sourcing-app.git \
  --branch main

# Install on site
docker compose exec backend bench --site erp.talent.amdg.ai install-app ai_talent_sourcing

# Run migrations
docker compose exec backend bench --site erp.talent.amdg.ai migrate
EOF
```

---

## Step 7: Copy Encryption Key (CRITICAL)

```bash
# Get encryption_key from Railway's site_config.json
# This is in the Railway container at:
#   /home/frappe/frappe-bench/sites/site1.local/site_config.json

# Set it on Hetzner:
ssh root@5.161.215.63 << 'EOF'
cd /opt/erpnext
docker compose exec backend bench --site erp.talent.amdg.ai \
  set-config encryption_key "PASTE_KEY_FROM_RAILWAY_HERE"
docker compose restart backend queue-worker scheduler
EOF
```

> **WARNING:** Without the encryption key, any encrypted fields (passwords, API keys stored in ERPNext) become unrecoverable.

---

## Step 8: DNS Cutover

1. In Cloudflare DNS for `amdg.ai`:
   - Add A record: `erp.talent` → `5.161.215.63` (proxy OFF initially for Caddy TLS)
2. Wait for propagation: `dig erp.talent.amdg.ai`
3. Caddy will auto-obtain TLS certificate on first request

---

## Step 9: Update Enrichment Worker

```bash
ssh root@5.161.215.63 << 'EOF'
cd /opt/enrichment-worker

# Update .env - change ERPNEXT_URL from Railway to local
sed -i 's|ERPNEXT_URL=.*|ERPNEXT_URL=http://backend:8000|' .env

# Restart enrichment worker
docker compose up -d --force-recreate
EOF
```

> The enrichment worker is on the same Docker network (`primitivemail_default`) so it can reach ERPNext's backend directly.

---

## Step 10: Smoke Test

```bash
# 1. Login via browser
open https://erp.talent.amdg.ai

# 2. Check Job Applicants exist
curl -s "https://erp.talent.amdg.ai/api/resource/Job%20Applicant?limit_page_length=5" \
  -H "Authorization: token API_KEY:API_SECRET"

# 3. Send test email through pipeline
python3 /opt/primitivemail/send_from_vps.py

# 4. Verify new Job Applicant created
# 5. Check enrichment worker logs
docker logs enrichment-worker --tail 50
```

---

## Step 11: Daily Backup Cron

```bash
ssh root@5.161.215.63 << 'EOF'
cat > /etc/cron.d/erpnext-backup << 'CRON'
# Daily ERPNext backup at 3 AM UTC
0 3 * * * root cd /opt/erpnext && docker compose exec -T backend bench --site erp.talent.amdg.ai backup --with-files 2>&1 | logger -t erpnext-backup
# Weekly cleanup of backups older than 30 days
0 4 * * 0 root find /opt/erpnext/sites/erp.talent.amdg.ai/private/backups -mtime +30 -delete 2>&1 | logger -t erpnext-backup-cleanup
CRON
chmod 644 /etc/cron.d/erpnext-backup
EOF
```

---

## Rollback

If anything goes wrong:

```bash
# 1. Revert enrichment worker to Railway URL
ssh root@5.161.215.63 "cd /opt/enrichment-worker && \
  sed -i 's|ERPNEXT_URL=.*|ERPNEXT_URL=https://erpnext-v16-talent-sourcing-production.up.railway.app|' .env && \
  docker compose up -d --force-recreate"

# 2. Remove DNS record for erp.talent.amdg.ai (Cloudflare)

# 3. Railway instance is still running — no action needed
```

---

## Decommission Railway (After 14 Days)

Only after 14 days of stable operation on Hetzner:

1. Take one final backup from Railway (insurance)
2. Stop Railway ERPNext service
3. Delete Railway MariaDB + Redis services
4. Remove Railway project (optional — keep for reference)

---

## RAM Budget (CX32 = 4 GB)

| Service | Limit | Expected |
|---------|-------|----------|
| MariaDB | 512 MB | 300-400 MB |
| Backend (gunicorn x2) | 512 MB | 200-400 MB |
| Frontend (nginx) | 128 MB | 30-50 MB |
| Websocket (node) | 128 MB | 40-60 MB |
| Queue Worker | 384 MB | 100-200 MB |
| Scheduler | 256 MB | 80-120 MB |
| Redis (cache) | 96 MB | 30-64 MB |
| Redis (queue) | 96 MB | 20-40 MB |
| Caddy | 64 MB | 15-30 MB |
| PrimitiveMail | - | 55 MB |
| Watcher | - | 102 MB |
| Enrichment Worker | - | 71 MB |
| **Total** | **~2.2 GB limits** | **~1.1-1.6 GB actual** |
| **Headroom** | - | **~2.4-2.9 GB free** |
| **Swap (emergency)** | 2 GB | Should stay near 0 |
