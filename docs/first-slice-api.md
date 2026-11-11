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
