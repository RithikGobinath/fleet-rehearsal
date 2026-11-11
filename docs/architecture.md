# Architecture and trust boundaries

Angular calls FastAPI under `/api`. PostgreSQL is the source of truth for inventory, observations, grants, approvals, reservations, and audit records. The planner consumes a snapshot and returns proposed waves and deferrals. A Python worker will rehearse approved revisions. Kafka will deliver observations after the transaction that accepts them commits.

| Role | Inventory and evidence | Propose | Approve | Rehearse |
| --- | --- | --- | --- | --- |
| Viewer | Granted tenant and sites | — | — | — |
| Partner proposer | Granted sites | Granted sites | — | — |
| Customer approver | Own tenant and assigned sites | — | Matching revision in scope | — |
| Operator | Assigned sites | — | — | Approved revision in scope |

- Codex owns the HTTP contract, shared fixtures, storage, orchestration, and release work. Claude Code owns the pure planner and its independent validation. Antigravity owns the console and browser tests.
- The Compose demo binds the browser to loopback and issues short-lived local bearer tokens for seeded identities. Grants are checked against database state on every read. The demo identity endpoint requires explicit local mode.
- Wave starts will revalidate freshness and the approved revision, then acquire the site reservation in the same transaction as the simulated start. An accepted observation that is still pending projection blocks a start.
- Keep production release credentials in GitHub's release identity. Agents use local worktrees and synthetic data. Hosted user authentication is a separate release requirement.
