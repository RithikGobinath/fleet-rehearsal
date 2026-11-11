# Worktrees and GitHub backlog

The repository is private. The worktrees were split at `6fa6439833acf2f37c754991103eadcb091bf0fe` with the scope, architecture, and inventory API proposal. The integration branch is based on merged planning commit `ad8fb40de7a857105e1c1531afa5809299757a72`.

| Lane | Checkout | Branch | Writes | Local web port if used |
| --- | --- | --- | --- | --- |
| Codex | `fleet-rehearsal` | `feat/first-inventory-slice` | API, data fixtures, contracts, CI, and integration | 8080 |
| Claude Code | `fleet-planner` | `feat/wave-planner` | Pure planner and planner tests | 8081 |
| Antigravity | `fleet-console` | `feat/inventory-console` | Angular console and browser tests; reads generated API types | 8082 |

Codex owns edits to the shared contract and generated types. Each lane links its issue, starting SHA, and check results when handing off. The planner and console lanes can run their checks without Compose. If they run the full stack, copy `.env.example` to `.env` in that checkout and set a distinct `COMPOSE_PROJECT_NAME` and the assigned `FLEET_WEB_PORT`. Set `FLEET_BASE_URL` to match when running browser tests.

The [GitHub milestones](https://github.com/RithikGobinath/fleet-rehearsal/milestones) group blocks. Use the [build-block issue template](../.github/ISSUE_TEMPLATE/build-block.md) and link prerequisites by issue number. Keep exactly one status label on open blocks:

- [Ready](https://github.com/RithikGobinath/fleet-rehearsal/issues?q=is%3Aissue+is%3Aopen+label%3Astatus%3Aready)
- [In progress](https://github.com/RithikGobinath/fleet-rehearsal/issues?q=is%3Aissue+is%3Aopen+label%3Astatus%3Ain-progress)
- [Review](https://github.com/RithikGobinath/fleet-rehearsal/issues?q=is%3Aissue+is%3Aopen+label%3Astatus%3Areview)
- [Blocked](https://github.com/RithikGobinath/fleet-rehearsal/issues?q=is%3Aissue+is%3Aopen+label%3Astatus%3Ablocked)
- [Done](https://github.com/RithikGobinath/fleet-rehearsal/issues?q=is%3Aissue+is%3Aclosed+reason%3Acompleted)
