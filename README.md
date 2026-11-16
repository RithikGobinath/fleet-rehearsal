# Fleet Rehearsal

Fleet Rehearsal is an independent portfolio project for planning and rehearsing maintenance across a modeled network. It shows why a wave can proceed or must pause, including when an alternate path goes down after approval.

The local inventory demo runs against seeded PostgreSQL data and public advisory evidence. It includes scoped demo identities, three advisory outcomes, and device evidence.

## Run the local demo

From the repository directory with Docker Compose available, run `docker compose up --build` and open [Fleet Rehearsal](http://127.0.0.1:8080). On this project's Windows workstation, run it from PowerShell with `wsl -d Ubuntu -u root -- docker compose up --build`. Keep that terminal running while using the demo.

- The defaults work without a local environment file. Copy `.env.example` to `.env` to override the local port or Compose project name.
- Select **Northstar operator** for Harbor and River, **Partner · Harbor** for Harbor only, or **Summit operator** for Ridge.
- Filter the inventory by finding and select a device to see the reason, source, and observation time.
- Run the API integration tests with `docker compose exec -T api python -m unittest discover -s tests -v`.
- For browser checks, run `npm ci --prefix web`, install Chromium with `npx playwright install chromium` from `web/`, then run `npm run test:e2e --prefix web` from the repository root.

To run the synthetic rehearsal worker, start with `docker compose --profile rehearsal up --build`. Open **Rehearsal** for Harbor. Preview as the Harbor partner, load and approve the same revision as the Northstar approver, then load and start as the Northstar operator. For the failure case, run `docker compose exec -T api python -m fleet.scenario peer-down` after approving and before starting. The wave blocks and the audit shows why. This scenario uses a fixed simulation clock and no device connections. Use `fleet.scenario healthy` to restore healthy evidence or `fleet.scenario healthy-next` to advance to the second window.

The Compose configuration uses Python 3.12 and Node.js 24 and installs dependencies from `api/requirements.lock` and `web/package-lock.json`. Its only published port binds to loopback. Demo identities grant access to synthetic data and the API requires explicit local mode. [Architecture and trust boundaries](docs/architecture.md)

For Angular work without Docker, run `npm ci --prefix web` and `npm run dev:fixtures --prefix web`, then open [the local fixture console](http://127.0.0.1:4200). It shows a **Local fixtures** badge and uses browser-only sample responses. The normal build calls FastAPI.

## Project plan

- [Operator guide](docs/operator-guide.md) runs the demo and verifies its behavior from a clone.
- [Portfolio walkthrough](docs/portfolio-walkthrough.md) follows the healthy and blocked rehearsals using the recorded browser evidence.
- [Release evidence and handoff](docs/release-evidence.md) covers the $0 candidate and the future AWS decision.
- [GitHub build map](docs/build-map.md) contains the flowcharts, dependencies, model assignments, and acceptance checks.
- [Release scope and acceptance](docs/scope.md) defines the maintenance rehearsal demo.
- [API contract](docs/first-slice-api.md) distinguishes the live inventory interface from planned rehearsal routes.
- [Worktree ownership and issue views](docs/delivery.md) explain how to pick up a block and hand it off.
