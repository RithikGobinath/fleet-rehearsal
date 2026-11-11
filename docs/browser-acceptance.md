# Rehearsal browser acceptance

Use the [casebook](casebook.md) and [API contract](../contracts/openapi.yaml). Existing Playwright coverage is for live inventory. Rehearsal journeys below become runnable when the planned routes, grants, and UI land.

## Inventory (live)

1. Select Northstar and see Harbor and River; switch to the Harbor partner and see only Harbor. Assert River details return `404` for the partner. Switch to Summit, see Ridge, and confirm Harbor rows and open detail state clear. A direct request to an ungranted tenant returns `403` without asset data.
2. In Harbor, check **Affected**, **Unaffected**, and **Needs Evidence**. Open **Details** with the keyboard and verify the reason, observation time, and linked advisory. The filters act on server results.

## Passing rehearsal (planned)

1. As `demo-partner` with a Harbor proposer grant, save the first `dispatch` preview with `base_revision: 0`. Assert `201`, revision 1, its fingerprint, and the two numbered waves with the blackout gap. Show `harbor-03` as deferred for `missing_evidence`.
2. As a separate Northstar customer approver granted Harbor, approve revision 1 with its fingerprint. Assert `201` and a `plan_approved` audit event. Reload the plan and show the same approval. The preview and approval each use their own idempotency key.
3. As the Harbor operator, start wave 1 against healthy snapshot 1. Assert `202`, one run ID, and a visible queued or running state. Retry the same request with the same key; assert the same run ID and location. Reusing the key with a different request returns `409 idempotency_conflict`.
4. Poll run status through `succeeded`. Show the successful `harbor-01` outcome and verify exactly one `simulator_started` audit event for the run. Wave 2 remains gated on fresh evidence and the successful canary.

## Blocked after approval (planned)

1. Begin with the same approved revision in a fresh scenario. Accept the fixture's peer-down observation. Before projection, the start is blocked with `409 pending_evidence` and no run.
2. After projection, submit a new start intent with a fresh key. At 19:01Z assert `409 alternate_path_unavailable`, snapshot revision 2, and an audit ID. Show that Dispatch would lose its remaining uplink, and leave the run view empty for this attempt.
3. Follow the ID in the scoped timeline and assert `wave_blocked`, wave 1, revision 1, snapshot 2, and no run ID. Confirm zero simulator starts and the approval remains unchanged.

## Denied and stale actions (planned)

- A partner cannot approve and the customer approver cannot start a run without an operator grant. Assert `403 forbidden`, no new approval or run, and an actionable message. An ungranted site returns `404` with no site or resource details.
- Supersede a revision, then attempt to approve or start the old one. Assert `409 stale_revision`, a reload action, and no mutation. A preview with no eligible waves shows its deferral reasons and cannot be approved.
