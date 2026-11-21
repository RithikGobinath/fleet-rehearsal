# Fleet Rehearsal

Fleet Rehearsal turns advisory findings into proposed maintenance waves and rehearses them against a synthetic network. It shows how one approved wave succeeds and how losing an alternate path can block another before an upgrade starts.

## Azure rehearsal results

On November 19, 2026, one Azure VM ran k3s with in-cluster PostgreSQL and Kafka and one rehearsal worker. Separate Auth0 demo identities proposed, approved, and operated two plans for the synthetic Harbor site.

- **Healthy canary.** `harbor-01` passed its boot check in 50 modeled seconds with seed `1`. All protected services were reachable, and the audit recorded one simulator start.
- **Peer outage.** After simulated device `harbor-02` went down, the runtime guard refused the second approved plan with `alternate_path_unavailable`. Audit `audit-8` records the refusal, and the plan had zero simulator starts.

Harbor was restored to its healthy fixture and the VM was deallocated after the run. Real hardware validation is pending.

[Azure deployment and capture runbook](deploy/azure-demo/README.md) · [Run summary](docs/evidence/azure-harbor/harbor-azure-rehearsal.json) · [Blocked-wave detail](docs/evidence/azure-harbor/harbor-blocked-wave.jpg)

The November 19 files are an operator-written summary and a cropped browser capture. The audit actor IDs are application principals. The runbook explains how to export API records and capture hosted sign-in evidence together. The release workflow uploads an API export from the separate local browser test.

## Run the demo

```sh
docker compose --profile rehearsal --profile events up --build
```

Open [the console](http://127.0.0.1:8080). In Harbor's **Rehearsal** panel, preview the plan as **Partner · Harbor**, load and approve that revision as the **Northstar approver**, then load and start it as the **Northstar operator**. Refresh the run to see the modeled upgrade duration, boot outcome and reachability check. On a fresh stack, the second wave discovers `harbor-02` through its affected inventory finding and site topology.

To test a lost path, run the following after approval and before starting the first wave of a fresh rehearsal. It updates Harbor's synthetic topology and advances its fixture clock.

```sh
docker compose exec -T api python -m fleet.scenario peer-down
```

Verify the API and worker with `docker compose exec -T api python -m unittest discover -s tests -v`. The [release workflow](.github/workflows/release-candidate.yml) also runs browser tests, verifies Kafka redelivery and smoke-tests the Kubernetes manifests in Kind.

The [architecture](docs/architecture.md) explains the simulator and includes a two-source Kafka exercise. The [decision record](docs/decisions.md) reports benchmark results and their scope. The [GitHub Pages walkthrough](https://rithikgobinath.github.io/fleet-rehearsal/) shows the synthetic rehearsal with browser evidence from GitHub Actions.
