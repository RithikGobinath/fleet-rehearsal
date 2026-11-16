# Run Fleet Rehearsal

Clone the repository with Git and start the synthetic stack from its root with Docker and Docker Compose.

```sh
docker compose --profile rehearsal --profile events up --build --wait
```

Open [the local console](http://127.0.0.1:8080). It binds to loopback. The database and Kafka broker are internal to Compose. Docker Compose without profiles starts the inventory alone. All identities, devices, telemetry, and actions in the demo are synthetic.

## Rehearse Harbor maintenance

1. Open **Rehearsal**. Select **Partner · Harbor**, give the plan a fresh ID, and choose **Create preview**. Wave 1 is the canary.
2. Select the Northstar approver, load the same revision, and approve it. Switch to the Northstar operator, load the revision, and start wave 1. Refresh the run after the worker processes it.
3. Prepare and approve a second plan. Before starting, run `docker compose exec -T api python -m fleet.scenario peer-down`. Refresh evidence and try to start wave 1. Open the audit view and locate the decision linked in the rejection. Use `fleet.scenario healthy` to restore the local fixture.

The automated browser journey also exercises the gap between acceptance and projection of a Kafka observation. Start it on a disposable Compose project with the broker but without background consumers. The test advances the relay, projector and worker explicitly so each check is reproducible.

```sh
COMPOSE_PROJECT_NAME=fleet-review docker compose --profile events up --build --wait kafka api web
npm ci --prefix web
cd web
npx playwright install chromium
COMPOSE_PROJECT_NAME=fleet-review FLEET_E2E_EVENTS=1 FLEET_RECORD_DEMO=1 npm run test:e2e -- rehearsal.spec.ts
```

If you already have a demo running, stop it first to release the local port. From the repo root, use `docker compose down` to stop the original project while retaining its data. When you finish the automated journey, `docker compose -p fleet-review --profile events down -v` removes only the throwaway test project and its volumes.

## Verify and contribute

| Check | Command or evidence |
| --- | --- |
| API, PostgreSQL, role and migration integration | `docker compose exec -T api python -m unittest discover -s tests -v` |
| API client drift | `npm run types:api --prefix web -- --check` |
| Actual broker redelivery | `docker compose exec -T -e FLEET_KAFKA_BOOTSTRAP=kafka:9092 api python -m unittest discover -s tests -p test_observations.py -k real_broker -v` |
| Kubernetes, scans and workload measurements | Review the **Release candidate checks** run for your exact commit. The jobs publish browser, recovery, security and workload artifacts. |

Use [the build map](build-map.md) to find each block and its acceptance test. Keep contract fields in [OpenAPI](../contracts/openapi.yaml) and regenerate the client after approved contract changes. Use [the delivery guide](delivery.md) for branch and review ownership.

## Diagnose a stopped rehearsal

- For startup, run `docker compose ps` and `docker compose logs api`. Check `/readyz` from inside the API container at port 8000. The web proxy exposes `/api/healthz` and keeps readiness and metrics internal.
- For a denied start, inspect the displayed reason and audit ID. Search the API logs and audit evidence using the response's `X-Request-ID`. The API uses `Idempotency-Key` for write retries. A fresh start against changed evidence uses a fresh key.
- For pending evidence, inspect the relay and projector logs and the unapplied observation count. Consult the [operations runbook](operations.md) for recovery steps and the Prometheus and Grafana configuration.

The current portfolio run uses local services and GitHub Actions for $0 in AWS spend. The optional 24-hour AWS sandbox is estimated at $12 and requires a scheduled approval and verified teardown. [AWS sandbox proposal](aws-sandbox.md)
