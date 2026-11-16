# Rehearsal browser acceptance

Use the [casebook](casebook.md) and [API contract](../contracts/openapi.yaml). GitHub Actions runs the live inventory and canary rehearsal journeys, including the pending and projected failure states. The API suite exercises worker recovery, permissions, and stale revisions.

## Inventory (live)

1. Select Northstar and see Harbor and River; switch to the Harbor partner and see only Harbor. Assert River details return `404` for the partner. Switch to Summit, see Ridge, and confirm Harbor rows and open detail state clear. A direct request to an ungranted tenant returns `403` without asset data.
2. In Harbor, check **Affected**, **Unaffected**, and **Needs Evidence**. Open **Details** with the keyboard and verify the reason, observation time, and linked advisory. The filters act on server results.

## Passing rehearsal

1. As `demo-partner` with a Harbor proposer grant, save the first `dispatch` preview with `base_revision: 0`. Assert `201`, revision 1, its fingerprint, and the two numbered waves with the blackout gap. Show `harbor-03` as deferred for `missing_evidence`.
2. As a separate Northstar customer approver granted Harbor, approve revision 1 with its fingerprint. Assert `201` and a `plan_approved` audit event. Reload the plan and show the same approval. The preview and approval each use their own idempotency key.
3. As the Harbor operator, start wave 1 against healthy snapshot 1. Assert `202`, one run ID, and a visible queued or running state. Retry the same request with the same key; assert the same run ID and location. Reusing the key with a different request returns `409 idempotency_conflict`.
4. Poll run status through `succeeded`. Show the successful `harbor-01` outcome and verify exactly one `simulator_started` audit event for the run. Wave 2 remains gated on fresh evidence and the successful canary.

## Blocked after approval

1. Begin with the same approved revision in a fresh scenario. Accept the fixture's peer-down observation. Before projection, the start is blocked with `409 pending_evidence` and no run.
2. After projection, submit a new start intent with a fresh key. At 19:01Z assert `409 alternate_path_unavailable`, snapshot revision 2, and an audit ID. Show that Dispatch would lose its remaining uplink, and leave the run view empty for this attempt.
3. Follow the ID in the scoped timeline and assert `wave_blocked`, wave 1, revision 1, snapshot 2, and no run ID. Confirm zero simulator starts and the approval remains unchanged.

### Queued run blocked before execution

Reserve a wave with a healthy peer and hold the simulated worker until the peer-down observation is applied. The original `202` and its replay both identify the same run. Poll that run to see `blocked`, `alternate_path_unavailable`, snapshot 2, an audit ID, and no device outcomes. Show **Blocked before simulation started**. Verify one matching `wave_blocked` event with the run ID and zero `simulator_started` events. Repeat with unchanged evidence aging out at 19:04:01Z, then separately with a worker first processing the queue at the exclusive wave end (19:05Z, `outside_window`). Each blocked attempt releases its reservation; replaying the original key cannot start it.

### Recovery after execution starts

Inject a failed canary verification after the simulator starts. Show the failed or rolled-back device outcome and recovery state. Assert the next wave returns `409 prerequisite_incomplete`, even after rollback, and that restarting the worker does not create a second `simulator_started` event for the first wave.

## Denied and stale actions

- A partner cannot approve and the customer approver cannot start a run without an operator grant. Assert `403 forbidden`, no new approval or run, and an actionable message. An ungranted site returns `404` with no site or resource details.
- Revoke the submitting operator's grant after reservation and retry its key. Assert the scoped denial without the old run or location, clear the now-inaccessible view, and check with an authorized actor that the queued run was blocked and never started.
- Supersede a revision, then attempt to approve or start the old one. Assert `409 stale_revision`, a reload action, and no mutation. A preview with no eligible waves shows its deferral reasons and cannot be approved.
