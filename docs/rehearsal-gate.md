# Rehearsal verification

The Harbor casebook links the canonical graph and policy to persisted plan revisions. The API test verifies two ordered waves, a missing-evidence deferral, and an impossible plan after the peer goes down. Planner tests compare the graph decision to a fixed-seed, independent reachability oracle. The live browser loads the saved preview, approves as a separate principal, and verifies both a simulated success and a peer-down block.

Claude reviewed a generic transaction outline; Codex checked the findings against the actual implementation and tests:

| Finding | Implementation and check |
| --- | --- |
| A grant or evidence update could race a start. | HTTP writes and the worker acquire the site row before locking and rechecking the grant. Synthetic evidence changes take the same site lock. Tests apply peer-down or revoke the grant after reservation and verify no start. |
| Two different sites could reuse an actor's key concurrently. | `rehearsal_receipts` uses one unique key per tenant, actor and action; insertion uses `ON CONFLICT DO NOTHING`, then compares the stored request fingerprint. The cross-site race returns one success and one conflict. |
| A restarted worker could duplicate a logical start. | The worker rereads the active run after locking the site. The start marker has a unique index per approved wave, and each state and audit update commits together. Tests inject an audit failure, resume across separate connections, and verify one start. |

Run the API suite from the [local demo instructions](../README.md) with the optional background worker stopped. Tests drive individual worker steps against PostgreSQL so the results do not depend on sleeps. The browser tests use the same maintainer scenario commands against the live API.
