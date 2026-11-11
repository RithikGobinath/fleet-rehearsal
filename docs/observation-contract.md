# Observation event contract

The [version 1 envelope](../contracts/observation-v1.schema.json) carries synthetic health evidence. The accepting API stamps `received_at` and verifies that the source is authorized for the tenant, site, and asset and that `observed_at <= received_at`. Source revisions are comparable only within the same tenant, asset, and source. Each required evidence input uses the source selected by site policy. The UTF-8 Kafka key is `tenant_id/asset_id` (the ID pattern excludes `/`).

The API will save the accepted event, the maximum accepted source revision, and its outbox row in one transaction. Retrying the same tenant and event ID with the same source payload returns the original receipt; a changed payload or reuse of a source revision with a different event ID conflicts. The relay publishes after commit and retries if acknowledgement is uncertain. The consumer deduplicates on tenant and event ID, applies only a newer source revision in the same transaction as the evidence projection, and acknowledges the broker offset after commit. An older revision can be recorded for audit, with no change to either watermark or the site snapshot.

| Sequence | Accepted watermark | Applied watermark | Rehearsal effect |
| --- | --- | --- | --- |
| Revision 4 accepted and projected | 4 | 4 | Evaluate freshness and policy against revision 4. |
| Revision 5 accepted, relay pending | 5 | 4 | Block with pending evidence for this dependency. |
| Revision 5 applied | 5 | 5 | Revalidate using the new observation. |
| The revision 5 event is delivered again | 5 | 5 | Preserve the same result. |
| A different event for revision 3 arrives late | 5 | 5 | Preserve the newer evidence. |

The [sample stream](../fixtures/observation-sequence.json) includes the duplicate delivery and a late healthy update after the peer is down. Applying the first two events produces site snapshot revisions 1 and 2 in the Harbor scenario; those revisions are independent of the per-source revision numbers 4 and 5.

The start transaction reads both watermarks under the same site guard used for relevant acceptance and projection. For each required input, accepted ahead of applied (including no applied value) blocks with `pending_evidence`; unrelated assets do not block the wave. Without pending data, missing evidence remains unusable and age is measured from `observed_at`, including the policy's exact upper bound. Receipt time is for audit, never a freshness reset. An invalid accepted event does not advance applied state; an explicitly repaired replay must succeed before it stops blocking a relevant start.

Start the broker and both observation workers with `docker compose --profile events up --build`. They use the internal `fleet-observations-v1` topic. A site operator submits observations at `POST /v1/tenants/{tenant_id}/sites/{site_id}/observations`. Use the fields in the envelope except tenant, site and receipt time, which the API supplies. The rehearsal panel reports pending and stale evidence for its authorized site.

The scoped quarantine list returns event IDs, reason and timestamps without storing or returning the rejected payload. Replay one repairable observation with `POST /v1/tenants/{tenant_id}/sites/{site_id}/observations/{event_id}/replay`. This republishes the original validated PostgreSQL copy; it does not edit evidence or bypass the revision check. The projector marks the failure repaired only after that copy is processed.
