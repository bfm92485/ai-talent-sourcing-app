# ADR-001: Migrate ERPNext from Railway to Hetzner VPS

## Status

Accepted

## Context

The AI Talent Sourcing pipeline runs across two providers:
- **Hetzner Cloud VPS** (5.161.215.63): PrimitiveMail (SMTP), Watcher, Enrichment Worker
- **Railway PaaS** (europe-west4): ERPNext v16 + MariaDB + Redis

This split introduces:
1. Cross-internet latency on every API call from enrichment worker to ERPNext
2. Higher cost (~$17-22/mo combined vs ~$6.50/mo consolidated)
3. Two failure domains instead of one
4. Network-level issues (IP rate limiting, firewall interference on SMTP DATA phase)

## Decision

Migrate ERPNext to the existing Hetzner VPS using official `frappe/erpnext` Docker images, consolidating all services on a single CX32 instance.

## Consequences

**Positive:**
- 65-75% cost reduction (~$6.50/mo total vs ~$20/mo)
- Near-zero latency for enrichment worker → ERPNext (Docker network)
- Single infrastructure to manage, monitor, and back up
- Full control over database, backups, and configuration

**Negative:**
- Single point of failure (mitigated by daily backups + swap)
- Maintenance burden shifts from Railway to self-managed
- RAM must be carefully managed (solved by CX32 upgrade + memory limits)

**Neutral:**
- MariaDB pinned to 10.6 (Frappe v16 tested version)
- Caddy chosen over Traefik (simpler config for single-service proxy)
- Combined queue worker (short+long) instead of separate (RAM optimization)

## Alternatives Considered

1. **Keep Railway** — Higher cost, cross-internet latency, but zero maintenance
2. **Hetzner CX22 (2 GB)** — Too tight for ERPNext + existing services
3. **Separate Hetzner VPS for ERPNext** — Unnecessary complexity for this scale
4. **Coolify/CapRover** — Extra abstraction layer not needed for 1 app

## References

- [frappe/frappe_docker](https://github.com/frappe/frappe_docker) — Official Docker deployment
- [Hetzner CX32 specs](https://www.hetzner.com/cloud) — 2 vCPU, 4 GB RAM, 80 GB, ~$6.50/mo
