# Synthetic casebook

[Inventory seed](../fixtures/inventory.json) creates Northstar's Harbor and River sites and Summit's Ridge site. The Northstar and Summit demo operators see their respective tenants; the partner sees Harbor only. Tenant, site, principal, and asset IDs are fixed. Startup applies [numbered SQL migrations](../api/fleet/migrations) and then inserts missing seed records in the same transaction under a database lock. A fresh database recreates the inventory; a restart preserves existing rows and migration timestamps. New seed rows keep the source evidence in JSON beside the normalized columns. For databases from before migration tracking, the upgrade reconstructs that JSON from the values already stored; the original input was not retained.

The applicability decision comes from the affected-products section of [Cisco advisory CVE-2025-20311, revision 1.1](https://www.cisco.com/c/en/us/support/docs/csa/cisco-sa-cat9k-PtmD7bgy.html). The modeled Catalyst 9300 rule combines affected software with the presence of an applicable port feature. Release labels, configuration observations, health, deadlines, and all customer data are invented.

| Asset | Site | Expected inventory finding |
| --- | --- | --- |
| `harbor-01` | Northstar / Harbor | `affected`: modeled affected release and an applicable feature present |
| `harbor-02` | Northstar / Harbor | `unaffected`: modeled fixed release |
| `harbor-03` | Northstar / Harbor | `needs_evidence`: incomplete feature observation |
| `river-01` | Northstar / River | `affected`: another applicable feature present |
| `ridge-01` | Summit / Ridge | `unaffected`: observed features do not meet the applicability rule |

The [Harbor rehearsal fixture](../fixtures/peer_down_after_approval.json) uses a separate health snapshot. It schedules synthetic routine maintenance, so advisory-unaffected `harbor-02` can still be a candidate. The two redundant uplinks serve `dispatch`. The planner evaluates snapshot 1 at 19:00Z and places the canary at 19:00–19:05Z, then the peer at 19:10–19:15Z after the blackout. The candidate deadlines model the ends of their coverage windows. `harbor-03` is deferred for missing health evidence. Later waves require fresh observations when they start.

The passing case starts wave 1 with the peer healthy. In the failing case, the same approved wave sees snapshot 2 at 19:01Z, after a down observation for `harbor-02` at 19:00:30Z. The validator returns `alternate_path_unavailable`. [The event fixture](../fixtures/observation-sequence.json) also shows the earlier pending phase and why a duplicate or late healthy report cannot reverse that decision.

For a short demo, start in Harbor as the partner, prepare the canary plan, then switch to the separate approver. Show the healthy control run first. In a fresh scenario, pause projection, accept the peer-down observation after approval, and show the blocked start while projection is pending. Resume projection and submit a new start intent. Show that the projected peer failure still blocks the run and the audit timeline keeps both decisions. Use the [browser acceptance journey](browser-acceptance.md) to reproduce the sequence.
