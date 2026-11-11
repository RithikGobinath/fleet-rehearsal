# Fleet Rehearsal release scope

Fleet Rehearsal helps a partner operator see which synthetic Catalyst 9300 assets require review and rehearse maintenance while preserving a modeled service's healthy uplink. A customer approver can review the plan revision. The release demo shows an approved wave stopping when its alternate path fails before the start.

## First usable slice

- Load two synthetic customer tenants across Harbor, River, and Ridge. Include affected, unaffected, and needs-evidence fixtures grounded in the applicability conditions in Cisco's public advisory. Use synthetic software labels and record their evidence.
- Let an operator select a local demo identity, browse only the sites assigned to that identity, filter devices by finding, and open the evidence behind a finding.
- Start the console, API, and PostgreSQL using Docker Compose. The hosted simulator follows after the workflow, isolation, recovery, and deployment checks in the build map. [Cisco advisory for CVE-2025-20311](https://www.cisco.com/c/en/us/support/docs/csa/cisco-sa-cat9k-PtmD7bgy.html)

## Rehearsal acceptance

1. With both paths healthy, a deterministic plan schedules one redundant switch at a time and preserves a modeled uplink throughout each wave.
2. After approval, a new observation shows the alternate path down. Revalidation returns `alternate_path_unavailable`. The audit view identifies the blocked wave and evidence revision, and the simulator records zero starts for the blocked wave.
3. A partner sees only explicitly granted sites. An actor from another tenant cannot retrieve the plan, approval, audit event, or device evidence.

## Release decision

The hosted portfolio release runs synthetic fleets and simulated device changes. The maintainer reviews the exact staged image digests, smoke and recovery results, evidence of tenant isolation, and the hosting cost before promotion. [Build map](build-map.md)
