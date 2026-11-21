# Decision record

Use a shared wave-start guard with a deadline- and reachability-aware planner. Scheduling can reduce rejected starts and device-hours of exposure. The guard provides admission safety for either schedule. Retain Kafka to ingest independently produced link and probe readings and replay them through one projector. PostgreSQL holds the applied evidence and rehearsal state.

## Planner measurement

The same five seeds generate 30 cases using one topology template with seven candidate devices, three redundant pairs and three hours per case. Observation ages vary by device, and some cases receive a seeded uplink outage. Both schedulers nominate the same canary. The runtime refusal cross-check implements the same rules for this pair topology. The harness uses fixed five-minute slots and counts admitted waves as completions; it measures scheduling independently of the simulator's boot and post-check outcomes.

| Measure | Canary-first severity order | Fleet Rehearsal |
| --- | ---: | ---: |
| Proposed waves | 163 | 149 |
| Proposals failing preview reachability or evidence checks | 20 | 0 |
| Starts rejected by the shared guard | 63 | 44 |
| `unsafe_starts_admitted` | 0 | 0 |
| Eligible affected devices completed by deadline | 100 / 170 | 105 / 170 |
| Affected device-hours until model completion or end of horizon | 267.75 | 253.00 |

Run `PYTHONPATH=api python scripts/benchmark-planner.py` for per-seed counts and refusal reasons.

## HTTP and deployment evidence

The release workflow attaches `api-latency.json` with measured response and scheduled-start p95, dispatch lag, errors and the source commit. It runs one API process over loopback against 10,000 seeded assets and rotates across four 25-row cursor positions at 25 requests per second. The default measured interval is 20 seconds after a five-second warmup. The 300 ms reference applies to scheduled-start p95 when dispatch is on pace. Request errors fail CI; timing misses and inconclusive pacing are recorded in the artifact. Grafana estimates the application-side p95 from histogram buckets, while the benchmark times the client through response receipt.

One local run completed all 500 reads and measured 45.96 ms p95 from scheduled start and 45.55 ms p95 from client dispatch.

Compose exercises PostgreSQL and Kafka locally, and the release workflow exercises Kubernetes in Kind. Live Auth0/OIDC was verified on one Azure VM running k3s for the synthetic Harbor rehearsal. The AWS Terraform has only been validated with the backend disabled and has never been applied. AWS spend for this work remains $0. EKS, RDS, and ECR publishing remain unverified. [Azure runbook](../deploy/azure-demo/README.md) · [November 19 evidence](evidence/azure-harbor/harbor-azure-rehearsal.json)
