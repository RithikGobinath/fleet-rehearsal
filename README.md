# Fleet Rehearsal

Fleet Rehearsal turns advisory findings into proposed maintenance waves and rehearses them against a synthetic network. It shows how one approved wave succeeds and how losing an alternate path can block another before an upgrade starts.

## Run the demo

```sh
docker compose --profile rehearsal --profile events up --build
```

Open [the console](http://127.0.0.1:8080). In Harbor's **Rehearsal** panel, preview the plan as **Partner · Harbor**, load and approve that revision as the **Northstar approver**, then load and start it as the **Northstar operator**. Refresh the run to see the modeled upgrade duration, boot outcome and reachability check. The fresh Harbor fixture includes two affected switches, one unaffected switch and one switch with incomplete evidence.

To test a lost path, run the following after approval and before starting the first wave of a fresh rehearsal. It updates Harbor's synthetic topology and advances its fixture clock.

```sh
docker compose exec -T api python -m fleet.scenario peer-down
```

Verify the API and worker with `docker compose exec -T api python -m unittest discover -s tests -v`. The [release workflow](.github/workflows/release-candidate.yml) also runs browser tests, verifies Kafka redelivery and smoke-tests the Kubernetes manifests in Kind.

The [architecture](docs/architecture.md) explains the simulator and includes a two-source Kafka exercise. The [decision record](docs/decisions.md) reports benchmark results and their scope. The [GitHub Pages walkthrough](https://rithikgobinath.github.io/fleet-rehearsal/) shows the synthetic rehearsal with browser evidence from GitHub Actions.
