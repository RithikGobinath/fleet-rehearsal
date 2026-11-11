# Cross-lane contract review

Claude Code reviewed the planner and the start boundary against a synthetic scenario. Antigravity reviewed the operator journey. Codex checked those findings against the repository's API, event schema, planner draft and casebook, then reconciled terminology and response shapes.

| Finding | Resolution |
| --- | --- |
| A rejected request and a worker rejection after reservation were indistinguishable in the run view. | The first returns `409` with an audit ID and creates no run. The second keeps the original `202` receipt and exposes a terminal `blocked` run, its audit ID, the evidence revision, and no device outcomes. |
| A queued wave could outlive the approved window or retain the only reservation after a worker stopped. | Recheck evidence age and the exclusive window end at execution. Under the site guard, expire unstarted reservations at the window end; a stale queued job cannot take over an expired reservation. |
| A retry could look like a new start or expose cached state after access changed. | Reauthorize before replay, replay only the original receipt, and use scoped status for the latest state. The worker checks the submitting actor's grants again. |
| A worker restart or a failed canary could be mistaken for another valid start. | Persist one simulator start and one committed outcome per step. A restarted worker continues the same run. Only a succeeded canary releases the next wave. |

The Harbor example keeps the approval on revision 1 in all branches. When the peer-down observation is accepted but not applied, the reason is `pending_evidence`. Once applied as snapshot 2, the planner returns `alternate_path_unavailable`. The API and browser acceptance use that exact reason and require no simulator start. See [API behavior](first-slice-api.md), [browser acceptance](browser-acceptance.md), and the [observation sequence](../fixtures/observation-sequence.json).

The runtime still needs the race and retry tests in FR-38, FR-39 and FR-41. The review verifies the contract and fixtures, not those implementations.
