# Inventory slice interface

The Angular console sends requests under `/api`. The development proxy or Compose reverse proxy removes `/api` before forwarding to FastAPI.

| Method and path | Response |
| --- | --- |
| `GET /healthz` | `{"status":"ok"}` |
| `GET /v1/demo/principals` | `{"items":[{"id":"demo-northstar","label":"Northstar operator","tenants":[{"id":"northstar","label":"Northstar"}]}]}`. Other seeded identities are `demo-summit` and `demo-partner`. |
| `POST /v1/demo/session` | Request `{"principal_id":"demo-northstar"}`. Response `{"access_token":"...","token_type":"bearer"}`. |
| `GET /v1/assets?tenant_id=northstar&finding=affected&limit=25&after=harbor-01` | `{"items":[Asset],"next_cursor":null,"total":1}`. `finding` and `after` are optional. |
| `GET /v1/assets/harbor-01?tenant_id=northstar` | One `Asset`. |

Asset list and detail require `Authorization: Bearer <access_token>`. Unauthenticated requests return 401. A tenant outside the caller's grants returns 403. A device outside the caller's site grants returns 404 even when the tenant itself is accessible. Errors have `{"detail":"human-readable message"}` for the first slice.

Each `Asset` contains the following fields.

```json
{
  "id": "harbor-01",
  "name": "Harbor access switch 01",
  "tenant_id": "northstar",
  "site_id": "harbor",
  "site_name": "Harbor",
  "product": "Catalyst 9300",
  "running_release": "lab-affected",
  "finding": "affected",
  "reason": "The modeled release is affected and an applicable feature is enabled.",
  "observed_at": "2026-11-10T18:00:00Z",
  "advisory_id": "CVE-2025-20311",
  "advisory_revision": "1.1",
  "advisory_url": "https://www.cisco.com/c/en/us/support/docs/csa/cisco-sa-cat9k-PtmD7bgy.html"
}
```

Finding values are `affected`, `unaffected`, and `needs_evidence`. The release labels are synthetic. Use `next_cursor` as `after` to fetch the next page. `total` counts the authorized rows matching the current filter before pagination.

## Rehearsal contract draft

[OpenAPI YAML](../contracts/openapi.yaml) references the live [inventory description](../contracts/openapi.json). The six operations marked `planned` in the YAML are preview, revision read, approval, rehearsal start, run status, and audit. They are specifications for the synthetic simulator. Validate from the repository root with `npx --yes @redocly/cli@2.58.2 lint --config contracts/redocly.yaml contracts/openapi.yaml`.

Codex owns the contract and generated Angular types. Run `npm ci --prefix web` and `npm run types:api --prefix web` after a contract change. The pinned generator follows both files; GitHub Actions uses `npm run types:api -- --check` to reject drift. Generated types describe the planned routes without exposing them in the runtime.

- **Authorization:** Check the bearer identity, tenant, site, and action permission before looking up any write result or returning resource details. No tenant grant or a missing action role returns `403`. An inaccessible site and a missing resource within the site scope both return `404`. Apply the [role matrix](architecture.md) to reads as well as writes.
- **Revision checks:** Preview requires the current `base_revision`; zero is only for a new plan. Approval and start identify a revision in the path and send its `expected_fingerprint`. Check both against the latest revision while holding the site guard. A mismatch returns `409 stale_revision` and, for an authorized caller, the current revision number to reload. The fingerprint binds tenant, site, plan, revision, policy and snapshot versions, waves, and deferrals. Approval does not change it. A superseding revision requires a new approval; approval without any eligible wave returns `409 invalid_plan_state`.
- **Retries:** All three writes require `Idempotency-Key`. Within a tenant, actor and operation, store the normalized scoped path and body fingerprint with the first committed outcome. Replay its status, body and resource location for the same key and request after rechecking current permissions. A different request with that key returns `409 idempotency_conflict`. Commit the result with the write, including a recorded blocked start. An intentional retry after evidence changes uses a fresh key. Concurrent duplicates wait for the recorded outcome or retry the same key; they never create a second run.
- **Revalidation:** Under the site guard, compare approval and policy, check the UTC window and earlier wave results, and recheck current evidence before reserving one active wave. Pending relevant evidence, stale evidence, or the loss of the last alternate path blocks the action. A recorded block returns `409` with an audit ID and no run. `202` reserves and queues one simulated run. The worker checks the same guards again at execution time, even if the snapshot is unchanged: evidence can expire in the queue. Commit the authorized simulator start and its audit event together; if the checks fail, pause the run and record the block without a simulator start. Only one run per plan revision and wave may enter the simulator. Recovery resumes that run or uses a newly approved plan revision.
- **Audit pages:** Order by the site's append sequence. `after` is an opaque cursor bound to the site scope; an invalid or mismatched cursor returns `422`. Scope and permissions are checked on each page. Only the scoped audit timeline exposes the blocked wave's reason and evidence.
