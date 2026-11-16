from bisect import bisect_left
from itertools import accumulate
from threading import Lock

from psycopg import Connection

from fleet.planner import (
    ALTERNATE_PATH_UNAVAILABLE, DOWN_CANDIDATE, MISSING_EVIDENCE, STALE_CANDIDATE,
)

BLOCK_REASONS = frozenset({
    ALTERNATE_PATH_UNAVAILABLE, DOWN_CANDIDATE, MISSING_EVIDENCE, STALE_CANDIDATE, "pending_evidence",
    "authorization_revoked", "outside_window", "site_busy", "prerequisite_incomplete", "stale_revision",
    "policy_changed", "inventory_changed", "already_rehearsed",
})
_latency_lock = Lock()
_buckets = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5)
_latency = {outcome: [0.0, [0] * (len(_buckets) + 1)] for outcome in ("2xx", "4xx", "5xx", "other")}


def observe_request(status: int, elapsed: float) -> None:
    outcome = f"{status // 100}xx"
    with _latency_lock:
        sample = _latency.get(outcome, _latency["other"])
        sample[0] += elapsed
        sample[1][bisect_left(_buckets, elapsed)] += 1


def render_metrics(db: Connection) -> str:
    lines = [
        "# TYPE fleet_observations_unpublished gauge",
        "# TYPE fleet_observations_unapplied gauge",
        "# TYPE fleet_observations_oldest_unapplied_seconds gauge",
        "# TYPE fleet_evidence_stale_sites gauge",
        "# TYPE fleet_rehearsal_blocked_total counter",
        "# TYPE fleet_rehearsal_failed_verifications_total counter",
        "# TYPE fleet_worker_heartbeat_age_seconds gauge",
    ]
    lines.append("# TYPE fleet_http_request_duration_seconds histogram")
    with _latency_lock:
        for outcome, (seconds, buckets) in _latency.items():
            metric = "fleet_http_request_duration_seconds"
            for bound, count in zip((*_buckets, "+Inf"), accumulate(buckets), strict=True):
                lines.append(f'{metric}_bucket{{outcome="{outcome}",le="{bound}"}} {count}')
            lines.append(f'{metric}_count{{outcome="{outcome}"}} {sum(buckets)}')
            lines.append(f'{metric}_sum{{outcome="{outcome}"}} {seconds:.6f}')
    observations = db.execute("""
        SELECT count(*) FILTER (WHERE published_at IS NULL) AS unpublished,
               count(*) FILTER (WHERE processed_at IS NULL) AS unapplied,
               coalesce(extract(epoch FROM (now()-min((body->>'received_at')::timestamptz)
                   FILTER (WHERE processed_at IS NULL))), 0)::float8 AS oldest
        FROM observation_events
    """).fetchone()
    lines.extend((f"fleet_observations_unpublished {observations['unpublished']}",
                  f"fleet_observations_unapplied {observations['unapplied']}",
                  f"fleet_observations_oldest_unapplied_seconds {max(0, observations['oldest'])}"))
    stale = db.execute("""
        SELECT count(*) AS count FROM rehearsal_sites r
        JOIN rehearsal_snapshots s ON (s.tenant_id,s.site_id,s.revision)=(r.tenant_id,r.site_id,r.snapshot_revision)
        WHERE EXISTS (
            SELECT 1 FROM jsonb_each(s.graph->'devices') AS device(id, evidence)
            WHERE (evidence->>'observed_at')::timestamptz
                  NOT BETWEEN r.clock_at - make_interval(secs => (s.graph->>'max_age_seconds')::float8) AND r.clock_at
        )
    """).fetchone()
    lines.append(f"fleet_evidence_stale_sites {stale['count']}")
    blocks = db.execute("""SELECT context->>'reason' AS reason,count(*) AS count FROM rehearsal_audit
                           WHERE kind='wave_blocked' GROUP BY 1""").fetchall()
    counts = dict.fromkeys((*BLOCK_REASONS, "other"), 0)
    for row in blocks:
        counts[row["reason"] if row["reason"] in BLOCK_REASONS else "other"] += row["count"]
    lines.extend(f'fleet_rehearsal_blocked_total{{reason="{reason}"}} {count}' for reason, count in sorted(counts.items()))
    failures = db.execute("""SELECT count(*) AS count FROM rehearsal_audit
                             WHERE kind='run_updated' AND context->>'reason'='verification_failed'""").fetchone()
    lines.append(f"fleet_rehearsal_failed_verifications_total {failures['count']}")
    workers = db.execute("SELECT worker,extract(epoch FROM now()-last_seen)::float8 AS age FROM worker_heartbeats")
    lines.extend(f'fleet_worker_heartbeat_age_seconds{{worker="{row["worker"]}"}} {max(0, row["age"])}' for row in workers)
    return "\n".join(lines) + "\n"
