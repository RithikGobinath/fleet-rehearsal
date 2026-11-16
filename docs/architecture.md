# Architecture

Angular calls the FastAPI service through the web container. Each API process uses a bounded PostgreSQL pool of 2–8 connections. PostgreSQL stores inventory, site grants, topology and health revisions, plan revisions, runs and audit events. Tenant and site grants control the proposer, approver and operator actions.

## Inventory to rehearsal

The inventory view applies the feature conditions from Cisco's advisory for CVE-2025-20311. The synthetic import supplies IOS XE release applicability. An affected device can enter the plan once it exists in that site's topology. Without an explicit policy entry, it gets priority 0 and the end of the last maintenance window as its deadline. Explicit site entries can set tighter deadlines, disable a device or require manual review. Affected devices missing from topology and policy entries with unaffected or incomplete inventory appear as deferrals. [Cisco advisory](https://sec.cloudapps.cisco.com/security/center/content/CiscoSecurityAdvisory/cisco-sa-cat9k-PtmD7bgy)

The planner schedules one device per wave, starting with a canary, inside maintenance windows and before patch deadlines. Revisions bind the plan to inventory evidence and the policy used at preview. Proposal checks use the preview snapshot. Later waves can outlive that evidence, so approval, reservation and the worker recheck their respective prerequisites. At start, the guard checks current evidence, the window, the canary prerequisite and reachability with the wave devices removed.

The worker normally seeds runs from their IDs and saves per-device draws in the start audit record so a restart can resume the same simulation. The draws cover durations of 45–120 modeled seconds and a 10% boot-failure chance. The local demo uses seed 1 for the healthy Harbor canary and still supports a forced failure scenario. A later worker tick evaluates the modeled completion against current applied topology and health. It traces each protected service to a healthy uplink and records unavailable devices and isolated services. On failure, the model tests whether restoring the previous image restores service. These are graph probes running on a simulated clock. Harbor is the only seeded rehearsal site.

## Observation inputs

The local Kafka projector accepts independently produced link-carrier and active-probe readings on separate topics. Each source orders its readings by revision. The projector combines the latest known readings, with a down result overriding an up result and the older timestamp bounding combined freshness. The HTTP observation endpoint also publishes accepted events via a PostgreSQL outbox. Accepted, unprojected HTTP evidence blocks starts. Direct stream readings affect the guard once committed to the database; a record still waiting in Kafka is invisible to it.

On a fresh Harbor fixture, run both commands with the events profile active. The healthy probe reading cannot clear the carrier outage. Reload the rehearsal panel to see the resulting decision. Send a link reading with revision `2`, `--carrier up` and timestamp `2026-11-10T18:59:50Z` to clear the outage.

```sh
docker compose exec -T observation-projector python -m fleet.observation_source link harbor-02 1 --carrier down --observed-at 2026-11-10T18:59:30Z
docker compose exec -T observation-projector python -m fleet.observation_source probe harbor-02 1 --sent 4 --received 4 --observed-at 2026-11-10T18:59:45Z
```

These producers send directly to Kafka and need no database credentials. The local projector scopes these topics to the synthetic `northstar/harbor` fixture. The hosted configuration uses the authenticated HTTP observation boundary.
