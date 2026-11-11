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

The Compose configuration uses Python 3.12 and Node.js 24 and installs dependencies from `api/requirements.lock` and `web/package-lock.json`. Its only published port binds to loopback. Demo identities grant access to synthetic data and the API requires explicit local mode. [Architecture and trust boundaries](docs/architecture.md)

## Project plan

- [GitHub build map](docs/build-map.md) contains the flowcharts, dependencies, model assignments, and acceptance checks.
- [Release scope and acceptance](docs/scope.md) defines the maintenance rehearsal demo.
- [Worktree ownership and issue views](docs/delivery.md) explain how to pick up a block and hand it off.
