"""
Persistent Queue Worker — uses Redis + RQ (Redis Queue) for crash-safe
email processing. PrimitiveMail explicitly has NO retry mechanism, so
we must build our own.

Design rationale:
- PrimitiveMail fires one-shot webhooks with no retry (confirmed via repo inspection)
- Download tokens expire in 15 minutes, so we must fetch attachments immediately
- The queue provides: crash recovery, retry with exponential backoff, dead letter queue
- Two-phase approach: (1) webhook handler immediately downloads + enqueues, (2) worker processes

Architecture:
  Webhook → [Download attachments immediately] → [Enqueue to Redis] → [RQ Worker processes]
                                                                         ↓
                                                              [Retry on failure (3x)]
                                                                         ↓
                                                              [Dead letter queue on exhaustion]

Why RQ over Celery:
- Simpler (single dependency: redis)
- Railway already provides Redis (same instance ERPNext uses for cache/queue)
- RQ's job persistence = crash recovery out of the box
- Adequate for our throughput (<100 emails/day)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from redis import Redis
from rq import Queue, Retry
from rq.job import Job

logger = logging.getLogger(__name__)

# Redis connection — reuse ERPNext's Redis or a dedicated one
REDIS_URL = os.environ.get("REDIS_QUEUE_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))

# Queue names
QUEUE_ENRICHMENT = "ats_enrichment"        # Main processing queue
QUEUE_DEAD_LETTER = "ats_dead_letter"      # Failed after max retries

# Retry configuration
MAX_RETRIES = 3
RETRY_INTERVALS = [60, 300, 900]  # 1min, 5min, 15min (exponential backoff)
JOB_TIMEOUT = 300  # 5 minutes max per job


def get_redis_connection() -> Redis:
    """Get a Redis connection from the configured URL."""
    return Redis.from_url(REDIS_URL)


def get_enrichment_queue() -> Queue:
    """Get the main enrichment processing queue."""
    return Queue(QUEUE_ENRICHMENT, connection=get_redis_connection())


def get_dead_letter_queue() -> Queue:
    """Get the dead letter queue for permanently failed jobs."""
    return Queue(QUEUE_DEAD_LETTER, connection=get_redis_connection())


def enqueue_enrichment_job(
    event_data: dict[str, Any],
    log_name: Optional[str] = None,
) -> Optional[str]:
    """
    Enqueue an email event for enrichment processing.

    Args:
        event_data: Serialized InboundEmailEvent (as dict) with attachments
                    already downloaded to local paths
        log_name: The ATS Inbound Email Log document name for status updates

    Returns:
        Job ID if successfully enqueued, None on failure
    """
    try:
        queue = get_enrichment_queue()
        job = queue.enqueue(
            "app.queue.tasks.process_enrichment_job",
            event_data,
            log_name,
            job_timeout=JOB_TIMEOUT,
            retry=Retry(max=MAX_RETRIES, interval=RETRY_INTERVALS),
            meta={
                "enqueued_at": datetime.now(timezone.utc).isoformat(),
                "idempotency_key": event_data.get("idempotency_key", ""),
                "message_id": event_data.get("message_id", ""),
            },
        )
        logger.info(f"Enqueued enrichment job: {job.id} (log: {log_name})")
        return job.id
    except Exception as e:
        logger.error(f"Failed to enqueue enrichment job: {e}")
        return None


def get_job_status(job_id: str) -> Optional[dict]:
    """Get the current status of a queued job."""
    try:
        conn = get_redis_connection()
        job = Job.fetch(job_id, connection=conn)
        return {
            "id": job.id,
            "status": job.get_status(),
            "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "ended_at": job.ended_at.isoformat() if job.ended_at else None,
            "result": job.result,
            "meta": job.meta,
            "exc_info": job.exc_info,
        }
    except Exception as e:
        logger.warning(f"Failed to fetch job {job_id}: {e}")
        return None


def get_queue_stats() -> dict:
    """Get current queue statistics for monitoring."""
    try:
        conn = get_redis_connection()
        enrichment_q = Queue(QUEUE_ENRICHMENT, connection=conn)
        dead_letter_q = Queue(QUEUE_DEAD_LETTER, connection=conn)
        return {
            "enrichment_queue": {
                "pending": enrichment_q.count,
                "failed": enrichment_q.failed_job_registry.count,
                "scheduled": enrichment_q.scheduled_job_registry.count,
            },
            "dead_letter_queue": {
                "count": dead_letter_q.count,
            },
        }
    except Exception as e:
        logger.warning(f"Failed to get queue stats: {e}")
        return {"error": str(e)}
