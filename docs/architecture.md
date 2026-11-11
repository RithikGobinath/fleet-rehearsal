# Architecture and trust boundaries

Angular calls FastAPI under `/api`. PostgreSQL is the source of truth for inventory, observations, grants, approvals, reservations, and audit records. The planner consumes a snapshot and returns proposed waves and deferrals. A Python worker will rehearse approved revisions. Kafka will deliver observations after the transaction that accepts them commits.

| Role | Inventory and evidence | Read plans, runs, and audit | Propose | Approve | Rehearse |
| --- | --- | --- | --- | --- | --- |
| Viewer | Granted sites | — | — | — | — |
| Partner proposer | Granted sites | Granted sites | Granted sites | — | — |
| Customer approver | Assigned sites in own tenant | Assigned sites in own tenant | — | Exact revision in scope, proposed by another actor | — |
| Operator | Assigned sites | Assigned sites | — | — | Approved revision in scope |

Resolve grants for the authenticated actor and the resource's tenant and site on every request. Site IDs are local to a tenant. A role on one site grants no actions on another. Audit rows are appended by the server as part of the recorded action.

### Entity keys

| Entity | Key and references |
| --- | --- |
| Tenant and site | Tenant uses `tenant_id`. Site uses `(tenant_id, site_id)`. |
| Asset | `(tenant_id, asset_id)`; references a site in the same tenant. |
| Advisory revision | `(advisory_id, revision)` identifies immutable public evidence. An asset finding links to the revision used for its evaluation. |
| Observation | `(tenant_id, event_id)`; references the asset and site. A source revision orders observations for one asset and source. |
| Topology snapshot | `(tenant_id, site_id, snapshot_revision)`; pins the topology and the applied evidence used to propose a wave. |
| Plan revision | `(tenant_id, site_id, plan_id, revision)`; immutable record of the snapshot, proposer, policy, and plan fingerprint. |
| Wave | Plan revision key plus `wave_number`; member assets must belong to the same site. Times use UTC. |
| Approval | Plan revision key; records the customer approver, fingerprint, and decision time. A new revision needs its own approval. |
| Rehearsal run | `(tenant_id, site_id, run_id)`; references the exact approved revision and wave and records the revalidation evidence. |
| Audit event | `(tenant_id, site_id, audit_id)`; records actor, outcome, and any affected revision, wave, run, and evidence references. A blocked start can have audit evidence without a simulator start. |

For example, `northstar/harbor` can have plan `dispatch` revision 1, wave 1, for asset `northstar/harbor-01`. The partner with a Harbor grant can propose that revision. A Northstar approver with a Harbor grant can approve it; a River-only approver cannot. A later Harbor observation can block the operator's start without altering the approved revision.

- Codex owns the HTTP contract, shared fixtures, storage, orchestration, and release work. Claude Code owns the pure planner and its independent validation. Antigravity owns the console and browser tests.
- The Compose demo binds the browser to loopback and issues short-lived local bearer tokens for seeded identities. Grants are checked against database state on every read. The demo identity endpoint requires explicit local mode.
- Wave starts will revalidate freshness and the approved revision, then acquire the site reservation in the same transaction as the simulated start. An accepted observation that is still pending projection blocks a start.
- Keep production release credentials in GitHub's release identity. Agents use local worktrees and synthetic data. Hosted user authentication is a separate release requirement.
