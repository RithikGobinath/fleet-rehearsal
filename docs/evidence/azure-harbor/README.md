# Azure Harbor rehearsal evidence

The November 20, 2026 repeat ran on one Azure VM in North Central US with Ubuntu 24.04, k3s v1.35.8+k3s1, in-cluster PostgreSQL and Kafka, and the synthetic Harbor site. Azure built and rolled out the API, web, and observation components from the GitHub `main` revision shown in the rollout output. [Deployed revision](https://github.com/RithikGobinath/fleet-rehearsal/commit/e91a481abcb3a4f19ebb3da67a6d3bb92eaf9e52) · [Azure rollout output](harbor-nov20-azure-rollout.txt) · [Runbook](../../../deploy/azure-demo/README.md)

## November 20 authenticated repeat

Both plans were proposed and approved while the topology was healthy at snapshot 7. The canary ran after the operator reserved it. Two one-off worker calls in the API pod used local mode to pin seed `1`. The canary on `harbor-01` passed its boot check in 50 modeled seconds with no unreachable services. After the simulated `harbor-02` outage advanced the topology to snapshot 8, the second plan returned HTTP 409 with `alternate_path_unavailable`. It has no reservation, simulator start, or run in the exported records. [Hosted API export](harbor-nov20-api-evidence.json)

| Browser request | Auth0 role | HTTP | Audit ID |
| --- | --- | ---: | --- |
| Preview healthy canary plan | Partner proposer | 201 | `audit-9` |
| Preview second plan | Partner proposer | 201 | `audit-10` |
| Approve healthy canary plan | Customer approver | 201 | `audit-11` |
| Approve second plan | Customer approver | 201 | `audit-12` |
| Reserve healthy canary | Operator | 202 | `audit-13` |
| Attempt second start after the outage | Operator | 409 | `audit-16` |

The accepted canary request ID also appears on `audit-14` (`simulator_started`) and `audit-15` (`run_updated`). In the export, each entry in `browser_requests` can be joined to `audit_events` on `correlation_id` and `audit_id`. The three Auth0 subjects were redacted with one key for this capture so repeated requests from the same role can be matched. The operator's redacted subject also matches the authenticated export request. [Audit and browser request records](harbor-nov20-api-evidence.json)

The JSON was collected with authenticated HTTPS GETs from the same browser origin. The hosted API accepted the Auth0 token and refused an anonymous context request with HTTP 401. The JSON includes the deployment commit, capture time, original audit and run fields, issuer, and redacted subject. Browser screenshots show the signed-in operator role. The JSON's `api_base` records the browser host.

The event times are November 20 in Chicago and November 21 from 01:15 to 01:22 UTC. The screenshots also show the simulator's fixture clock, which controls the modeled maintenance windows.

### Screenshots

| Capture | What it shows |
| --- | --- |
| [Azure rollout](harbor-nov20-azure-rollout.jpg) | Images imported and deployments updated on the VM. The [rollout output](harbor-nov20-azure-rollout.txt) gives the full Git SHA. |
| [Approved canary](harbor-nov20-01-approved.jpg) | The signed-in operator loaded the approved canary while the topology was healthy. |
| [Queued canary](harbor-nov20-02-queued.jpg) | The browser reserved Wave 1 before the one-off worker ran. |
| [Successful canary](harbor-nov20-03-succeeded.jpg) | Boot passed in 50 modeled seconds and all protected services were reachable. |
| [Blocked start](harbor-nov20-04-blocked.jpg) | The operator received the denial and `audit-16`. |
| [Full audit timeline](harbor-nov20-05-audit.jpg) | The signed-in operator loaded the original and repeat events for Harbor. |
| [Healthy fixture restored](harbor-nov20-azure-restored.jpg) | Snapshot 9 was healthy and the single worker rolled out again. |
| [Azure VM deallocated](harbor-nov20-azure-deallocated.jpg) | Azure reported `Stopped (deallocated)` after the capture. |
| [November 19 audit view](harbor-nov19-audit-operator-full.jpg) | The November 20 operator session retrieved the original blocked plan and full audit. |

## November 19 original run

The original API records contain eight events and one successful run. Both previews and approvals took place around 18:46 UTC on November 19, alongside the four recorded Auth0 sign-ins. The start attempts occurred at 04:54–04:55 UTC on November 20, still November 19 in Chicago. The Auth0 claim excerpt in this export describes the November 20 collection session. [Original API records](harbor-nov19-api-evidence.json) · [Auth0 sign-ins](auth0-login-events.json)

Both rehearsals exercised the simulator against one synthetic site. Real hardware validation is pending.
