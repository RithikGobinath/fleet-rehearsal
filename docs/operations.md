# Operate the synthetic fleet

The API's internal port serves `/readyz` after it reaches PostgreSQL and applies all numbered migrations. The web proxy does not expose `/readyz` or `/metrics`. Prometheus can scrape the API inside the application network with [the example configuration](../ops/prometheus.yml). Import [the dashboard](../ops/grafana-fleet.json) with Prometheus as the default data source.

| Signal | First response |
| --- | --- |
| API unreachable for one minute | Inspect the startup log and database connectivity. If migration failed, keep the candidate out of service and examine the migration transaction. |
| Oldest unapplied event above 60 seconds for one minute | Check the relay, broker and projector. Inspect the scoped quarantine for repairable events before attempting replay. |
| A worker heartbeat older than 30 seconds for one minute | Inspect the worker process, database and broker. Restart the worker after the dependency recovers. |
| `alternate_path_unavailable` or `pending_evidence` | The application rejected a rehearsal. Read its audit event and observation state before submitting a new start. |

Use the response's `X-Request-ID` to find the route template, status and duration in the API log. Successful writes and recorded blocks use that ID in their audit event. A repeated idempotency key returns the original decision, so its audit correlation ID remains the one from the original request. API logs use route templates and metrics aggregate by outcome, worker type or fixed reason.
