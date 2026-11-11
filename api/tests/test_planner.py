"""Tests for the pure wave planner and its independent validator.

Run from the repository root:  python -m unittest discover -s api/tests
"""

import copy
import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fleet.planner import normalize_snapshot, plan_waves, validate_wave  # noqa: E402

AT = "2026-11-10T18:00:00Z"
FRESH = "2026-11-10T17:55:00Z"
ALLOWED = {"allowed": True, "reason": None}


def rejected(reason):
    return {"allowed": False, "reason": reason}


def device(healthy=True, observed_at=FRESH, group=None):
    entry = {"healthy": healthy, "observed_at": observed_at}
    if group is not None:
        entry["redundancy_group"] = group
    return entry


def redundant_site(revision=1):
    """edge-1 is the uplink; access-1 hosts "pos" and reaches it through dist-a or dist-b."""
    return {
        "site_id": "harbor",
        "revision": revision,
        "devices": {
            "edge-1": device(),
            "dist-a": device(group="dist"),
            "dist-b": device(group="dist"),
            "access-1": device(),
        },
        "links": [
            ["edge-1", "dist-a"],
            ["edge-1", "dist-b"],
            ["dist-a", "access-1"],
            ["dist-b", "access-1"],
        ],
        "uplinks": ["edge-1"],
        "services": {"pos": "access-1"},
        "max_age_seconds": 3600,
    }


def ring_site():
    """edge-1 - r1 - r2 - r3 - edge-1 is a physical cycle; "crm" is attached to r2."""
    return {
        "site_id": "river",
        "revision": 1,
        "devices": {d: device() for d in ("edge-1", "r1", "r2", "r3")},
        "links": [["edge-1", "r1"], ["r1", "r2"], ["r2", "r3"], ["r3", "edge-1"]],
        "uplinks": ["edge-1"],
        "services": {"crm": "r2"},
        "max_age_seconds": 3600,
    }


def leaf_site(*leaves):
    """Leaves hang off edge-1, which is both the uplink and the "mgmt" attachment."""
    return {
        "site_id": "ridge",
        "revision": 1,
        "devices": {d: device() for d in ("edge-1",) + leaves},
        "links": [["edge-1", leaf] for leaf in leaves],
        "uplinks": ["edge-1"],
        "services": {"mgmt": "edge-1"},
        "max_age_seconds": 3600,
    }


def random_site(rng):
    ids = [f"d{i}" for i in range(rng.randint(2, 7))]
    return {
        "site_id": "oracle",
        "revision": 1,
        "devices": {d: device(healthy=rng.random() < 0.85) for d in ids},
        "links": [[a, b] for i, a in enumerate(ids) for b in ids[i + 1:] if rng.random() < 0.4],
        "uplinks": rng.sample(ids, rng.randint(1, 2)),
        "services": {f"svc{i}": rng.choice(ids) for i in range(rng.randint(1, 3))},
        "max_age_seconds": 3600,
    }


def oracle_keeps_services_reachable(snapshot, removed):
    """Union-find connectivity, deliberately unlike the validator's graph search."""
    parent = {d: d for d, e in snapshot["devices"].items() if e["healthy"] and d not in removed}

    def root(d):
        while parent[d] != d:
            d = parent[d]
        return d

    for a, b in snapshot["links"]:
        if a in parent and b in parent:
            parent[root(a)] = root(b)
    uplink_roots = {root(u) for u in snapshot["uplinks"] if u in parent}
    return all(d in parent and root(d) in uplink_roots for d in snapshot["services"].values())


def wave(*device_ids, starts_at="2026-11-10T20:00:00Z", ends_at="2026-11-10T21:00:00Z"):
    return {"device_ids": list(device_ids), "starts_at": starts_at, "ends_at": ends_at}


def candidate(cid, deadline="2026-11-12T00:00:00Z", criticality=1, eligible=True):
    return {"id": cid, "deadline": deadline, "criticality": criticality, "eligible": eligible}


def policy(*candidates, **overrides):
    result = {
        "candidates": list(candidates),
        "windows": [{"starts_at": "2026-11-10T20:00:00Z", "ends_at": "2026-11-11T06:00:00Z"}],
        "blackouts": [],
        "duration_seconds": 3600,
    }
    result.update(overrides)
    return result


def scheduled(plan):
    return [(w["device_ids"][0], w["starts_at"], w["ends_at"]) for w in plan["waves"]]


def reasons(plan):
    return {d["id"]: d["reason"] for d in plan["deferred"]}


class ValidateWaveTests(unittest.TestCase):
    def test_missing_alternate_link_blocks_a_wave(self):
        snapshot = redundant_site()
        snapshot["links"] = [edge for edge in snapshot["links"] if "dist-b" not in edge]
        self.assertEqual(validate_wave(snapshot, wave("dist-a"), AT), rejected("alternate_path_unavailable"))

    def test_healthy_control_allows_either_redundant_switch(self):
        for device_id in ("dist-a", "dist-b"):
            with self.subTest(device_id):
                self.assertEqual(validate_wave(redundant_site(), wave(device_id), AT), ALLOWED)

    def test_peer_down_after_approval_blocks_the_approved_wave(self):
        plan = plan_waves(redundant_site(), policy(candidate("dist-a"), candidate("dist-b")), AT)
        approved = plan["waves"][0]
        self.assertEqual(approved["device_ids"], ["dist-a"])
        self.assertEqual(validate_wave(redundant_site(), approved, AT), ALLOWED)

        later = redundant_site(revision=2)
        later["devices"]["dist-b"] = device(healthy=False, observed_at="2026-11-10T18:20:00Z", group="dist")
        recheck_at = "2026-11-10T18:25:00Z"
        self.assertEqual(validate_wave(later, approved, recheck_at), rejected("alternate_path_unavailable"))
        self.assertEqual(validate_wave(later, plan["waves"][1], recheck_at), rejected("down_candidate"))

    def test_stale_peer_blocks_the_wave_and_stale_candidate_is_rejected(self):
        snapshot = redundant_site()
        snapshot["devices"]["dist-b"]["observed_at"] = "2026-11-10T16:59:59Z"  # one second past max age
        self.assertEqual(validate_wave(snapshot, wave("dist-a"), AT), rejected("alternate_path_unavailable"))
        self.assertEqual(validate_wave(snapshot, wave("dist-b"), AT), rejected("stale_candidate"))

    def test_freshness_boundaries(self):
        snapshot = redundant_site()
        snapshot["devices"]["dist-b"]["observed_at"] = "2026-11-10T17:00:00Z"  # exactly max age
        self.assertEqual(validate_wave(snapshot, wave("dist-b"), AT), ALLOWED)
        snapshot["devices"]["dist-b"]["observed_at"] = "2026-11-10T18:00:01Z"  # after the evaluation time
        self.assertEqual(validate_wave(snapshot, wave("dist-b"), AT), rejected("stale_candidate"))

    def test_down_candidate_is_rejected(self):
        snapshot = redundant_site()
        snapshot["devices"]["dist-a"]["healthy"] = False
        self.assertEqual(validate_wave(snapshot, wave("dist-a"), AT), rejected("down_candidate"))

    def test_missing_evidence_fails_closed(self):
        broken_evidence = (
            {"observed_at": FRESH},
            {"healthy": True},
            {"healthy": "yes", "observed_at": FRESH},
            {"healthy": True, "observed_at": "2026-11-10T17:55:00"},
        )
        for evidence in broken_evidence:
            with self.subTest(evidence=evidence):
                snapshot = redundant_site()
                snapshot["devices"]["dist-a"] = dict(evidence, redundancy_group="dist")
                self.assertEqual(validate_wave(snapshot, wave("dist-a"), AT), rejected("missing_evidence"))
                self.assertEqual(validate_wave(snapshot, wave("dist-b"), AT), rejected("alternate_path_unavailable"))

    def test_invalid_waves(self):
        cases = {
            "same redundancy group": wave("dist-a", "dist-b"),
            "unknown device": wave("dist-z"),
            "duplicate device": wave("dist-a", "dist-a"),
            "no devices": wave(),
            "device_ids not a list": dict(wave("dist-a"), device_ids="dist-a"),
            "missing ends_at": {"device_ids": ["dist-a"], "starts_at": "2026-11-10T20:00:00Z"},
            "ends before start": wave("dist-a", starts_at="2026-11-10T21:00:00Z", ends_at="2026-11-10T20:00:00Z"),
            "empty interval": wave("dist-a", starts_at="2026-11-10T20:00:00Z", ends_at="2026-11-10T20:00:00Z"),
            "naive time": wave("dist-a", starts_at="2026-11-10T20:00:00"),
            "non-UTC offset": wave("dist-a", starts_at="2026-11-10T20:00:00+01:00"),
            "already ended": wave("dist-a", starts_at="2026-11-10T16:00:00Z", ends_at="2026-11-10T17:00:00Z"),
        }
        for name, case in cases.items():
            with self.subTest(name):
                self.assertEqual(validate_wave(redundant_site(), case, AT), rejected("invalid_wave"))

    def test_wave_cutting_both_paths_loses_reachability(self):
        snapshot = redundant_site()
        for device_id in ("dist-a", "dist-b"):
            del snapshot["devices"][device_id]["redundancy_group"]
        self.assertEqual(validate_wave(snapshot, wave("dist-a", "dist-b"), AT), rejected("alternate_path_unavailable"))

    def test_down_uplink_loses_reachability(self):
        snapshot = redundant_site()
        snapshot["devices"]["edge-1"]["healthy"] = False
        self.assertEqual(validate_wave(snapshot, wave("dist-a"), AT), rejected("alternate_path_unavailable"))

    def test_cycle_provides_the_alternate_path(self):
        site = ring_site()
        self.assertEqual(validate_wave(site, wave("r1"), AT), ALLOWED)
        self.assertEqual(validate_wave(site, wave("r3"), AT), ALLOWED)
        self.assertEqual(validate_wave(site, wave("r2"), AT), rejected("alternate_path_unavailable"))
        site["devices"]["r3"]["healthy"] = False
        self.assertEqual(validate_wave(site, wave("r1"), AT), rejected("alternate_path_unavailable"))

    def test_isolated_cycle_terminates_and_is_unreachable(self):
        site = ring_site()
        site["devices"].update({d: device() for d in ("x", "y", "z")})
        site["links"] += [["x", "y"], ["y", "z"], ["z", "x"]]
        site["services"]["lab"] = "y"
        self.assertEqual(validate_wave(site, wave("r1"), AT), rejected("alternate_path_unavailable"))

    def test_invalid_configuration_fails_closed(self):
        def broken(change):
            snapshot = redundant_site()
            change(snapshot)
            return snapshot

        cases = {
            "not a dict": [],
            "revision zero": broken(lambda s: s.update(revision=0)),
            "boolean revision": broken(lambda s: s.update(revision=True)),
            "missing max age": broken(lambda s: s.pop("max_age_seconds")),
            "link to unknown device": broken(lambda s: s["links"].append(["dist-a", "ghost"])),
            "self link": broken(lambda s: s["links"].append(["dist-a", "dist-a"])),
            "unknown uplink": broken(lambda s: s.update(uplinks=["ghost"])),
            "no uplinks": broken(lambda s: s.update(uplinks=[])),
            "no services": broken(lambda s: s.update(services={})),
            "service on unknown device": broken(lambda s: s["services"].update(pos="ghost")),
            "bad redundancy group": broken(lambda s: s["devices"]["dist-a"].update(redundancy_group=7)),
        }
        for name, snapshot in cases.items():
            with self.subTest(name):
                self.assertEqual(validate_wave(snapshot, wave("dist-a"), AT), rejected("invalid_configuration"))
        for at in ("2026-11-10T18:00:00", "not a time", None):
            with self.subTest(at=at):
                self.assertEqual(validate_wave(redundant_site(), wave("dist-a"), at), rejected("invalid_configuration"))

    def test_matches_union_find_oracle_on_random_graphs(self):
        rng = random.Random(20261110)
        for _ in range(300):
            snapshot = random_site(rng)
            target = rng.choice(sorted(snapshot["devices"]))
            if not snapshot["devices"][target]["healthy"]:
                expected = rejected("down_candidate")
            elif oracle_keeps_services_reachable(snapshot, {target}):
                expected = ALLOWED
            else:
                expected = rejected("alternate_path_unavailable")
            self.assertEqual(validate_wave(snapshot, wave(target), AT), expected, snapshot)


class SnapshotTests(unittest.TestCase):
    def test_equivalent_topology_has_identical_snapshot_bytes(self):
        first = redundant_site()
        second = copy.deepcopy(first)
        second["devices"] = dict(reversed(list(second["devices"].items())))
        second["links"] = [edge[::-1] for edge in reversed(second["links"])] + [first["links"][0]]
        second["uplinks"] = list(reversed(second["uplinks"])) + [second["uplinks"][0]]
        second["devices"]["dist-a"]["observed_at"] = FRESH.replace("Z", "+00:00")
        self.assertEqual(json.dumps(normalize_snapshot(first)), json.dumps(normalize_snapshot(second)))

    def test_unknown_evidence_is_not_assumed_healthy(self):
        snapshot = redundant_site()
        snapshot["devices"]["dist-b"] = {"healthy": "yes", "observed_at": "2026-11-10T17:55:00"}
        normalized = normalize_snapshot(snapshot)
        self.assertIsNone(normalized["devices"]["dist-b"]["healthy"])
        self.assertIsNone(normalized["devices"]["dist-b"]["observed_at"])
        self.assertEqual(validate_wave(normalized, wave("dist-a"), AT), rejected("alternate_path_unavailable"))


class PlanWavesTests(unittest.TestCase):
    def test_orders_by_deadline_criticality_and_id_with_canary_first(self):
        rules = policy(
            candidate("c"),
            candidate("b", deadline="2026-11-11T12:00:00Z"),
            candidate("a", criticality=5),
            candidate("d"),
            candidate("e", deadline="2026-11-13T00:00:00Z", criticality=0),
            canary_id="e",
        )
        plan = plan_waves(leaf_site("a", "b", "c", "d", "e"), rules, AT)
        self.assertEqual([w["device_ids"] for w in plan["waves"]], [["e"], ["b"], ["a"], ["c"], ["d"]])
        self.assertEqual([w["canary"] for w in plan["waves"]], [True, False, False, False, False])
        self.assertEqual(scheduled(plan)[0], ("e", "2026-11-10T20:00:00Z", "2026-11-10T21:00:00Z"))
        self.assertEqual(scheduled(plan)[-1], ("d", "2026-11-11T00:00:00Z", "2026-11-11T01:00:00Z"))
        self.assertEqual(plan["deferred"], [])

    def test_same_inputs_in_any_order_give_the_same_plan(self):
        snapshot = redundant_site()
        rules = policy(
            candidate("dist-a"),
            candidate("dist-b", criticality=3),
            candidate("access-1"),
            candidate("edge-1", eligible=False),
            windows=[
                {"starts_at": "2026-11-11T01:00:00Z", "ends_at": "2026-11-11T03:00:00Z"},
                {"starts_at": "2026-11-10T20:00:00Z", "ends_at": "2026-11-10T22:00:00Z"},
            ],
        )
        first = plan_waves(snapshot, rules, AT)
        self.assertEqual([w["device_ids"] for w in first["waves"]], [["dist-b"], ["dist-a"]])
        self.assertEqual(reasons(first), {"access-1": "alternate_path_unavailable", "edge-1": "not_eligible"})

        shuffled_snapshot = redundant_site()
        shuffled_snapshot["devices"] = dict(reversed(list(shuffled_snapshot["devices"].items())))
        shuffled_snapshot["links"] = [list(reversed(link)) for link in reversed(shuffled_snapshot["links"])]
        shuffled_rules = copy.deepcopy(rules)
        shuffled_rules["candidates"].reverse()
        shuffled_rules["windows"].reverse()

        self.assertEqual(plan_waves(snapshot, rules, AT), first)
        self.assertEqual(plan_waves(shuffled_snapshot, shuffled_rules, AT), first)
        self.assertEqual(plan_waves(snapshot, rules, "2026-11-10T18:00:00+00:00"), first)

    def test_fingerprint_changes_with_snapshot_revision(self):
        rules = policy(candidate("dist-a"))
        first = plan_waves(redundant_site(revision=1), rules, AT)
        second = plan_waves(redundant_site(revision=2), rules, AT)
        self.assertEqual(first["waves"], second["waves"])
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertRegex(first["fingerprint"], r"^[0-9a-f]{64}$")

    def test_fits_windows_around_blackouts(self):
        rules = policy(
            candidate("a"),
            candidate("b"),
            candidate("c"),
            candidate("d"),
            windows=[
                {"starts_at": "2026-11-11T01:00:00Z", "ends_at": "2026-11-11T03:00:00Z"},
                {"starts_at": "2026-11-10T23:00:00Z", "ends_at": "2026-11-10T23:30:00Z"},  # too short
                {"starts_at": "2026-11-10T20:00:00Z", "ends_at": "2026-11-10T22:00:00Z"},
            ],
            blackouts=[{"starts_at": "2026-11-10T20:30:00Z", "ends_at": "2026-11-10T21:00:00Z"}],
        )
        plan = plan_waves(leaf_site("a", "b", "c", "d"), rules, AT)
        self.assertEqual(scheduled(plan), [
            ("a", "2026-11-10T21:00:00Z", "2026-11-10T22:00:00Z"),
            ("b", "2026-11-11T01:00:00Z", "2026-11-11T02:00:00Z"),
            ("c", "2026-11-11T02:00:00Z", "2026-11-11T03:00:00Z"),
        ])
        self.assertEqual(plan["deferred"], [{"id": "d", "reason": "no_window_before_deadline"}])

    def test_touching_windows_merge_and_blackout_edges_are_half_open(self):
        rules = policy(
            candidate("a"),
            candidate("b"),
            windows=[
                {"starts_at": "2026-11-10T20:00:00Z", "ends_at": "2026-11-10T20:30:00Z"},
                {"starts_at": "2026-11-10T20:30:00Z", "ends_at": "2026-11-10T23:00:00Z"},
            ],
            blackouts=[{"starts_at": "2026-11-10T21:00:00Z", "ends_at": "2026-11-10T22:00:00Z"}],
        )
        plan = plan_waves(leaf_site("a", "b"), rules, AT)
        self.assertEqual(scheduled(plan), [
            ("a", "2026-11-10T20:00:00Z", "2026-11-10T21:00:00Z"),
            ("b", "2026-11-10T22:00:00Z", "2026-11-10T23:00:00Z"),
        ])

    def test_deadlines(self):
        rules = policy(
            candidate("late", deadline="2026-11-10T18:30:00Z"),  # cannot finish even if started now
            candidate("tight", deadline="2026-11-10T20:30:00Z"),  # the first slot ends at 21:00
            candidate("exact", deadline="2026-11-10T21:00:00Z"),  # ends exactly at its deadline
        )
        plan = plan_waves(leaf_site("late", "tight", "exact"), rules, AT)
        self.assertEqual(scheduled(plan), [("exact", "2026-11-10T20:00:00Z", "2026-11-10T21:00:00Z")])
        self.assertEqual(reasons(plan), {"late": "deadline_missed", "tight": "no_window_before_deadline"})

    def test_judges_health_at_evaluation_time_not_wave_time(self):
        snapshot = leaf_site("a")
        snapshot["max_age_seconds"] = 900
        rules = policy(
            candidate("a", deadline="2026-11-13T00:00:00Z"),
            windows=[{"starts_at": "2026-11-12T20:00:00Z", "ends_at": "2026-11-12T22:00:00Z"}],
        )
        plan = plan_waves(snapshot, rules, AT)
        self.assertEqual(scheduled(plan), [("a", "2026-11-12T20:00:00Z", "2026-11-12T21:00:00Z")])
        # The same evidence is stale by the scheduled start, which revalidation before the start catches.
        start = plan["waves"][0]["starts_at"]
        self.assertEqual(validate_wave(snapshot, plan["waves"][0], start), rejected("stale_candidate"))

    def test_never_schedules_before_evaluation_time(self):
        snapshot = leaf_site("a")
        snapshot["max_age_seconds"] = 4 * 3600
        plan = plan_waves(snapshot, policy(candidate("a")), "2026-11-10T20:15:00Z")
        self.assertEqual(scheduled(plan), [("a", "2026-11-10T20:15:00Z", "2026-11-10T21:15:00Z")])

    def test_defers_with_validator_reasons_and_never_emits_rejected_waves(self):
        snapshot = leaf_site("ok", "down", "stale", "blind", "skip")
        snapshot["devices"]["down"]["healthy"] = False
        snapshot["devices"]["stale"]["observed_at"] = "2026-11-10T12:00:00Z"
        del snapshot["devices"]["blind"]["healthy"]
        rules = policy(
            *(candidate(d) for d in ("ok", "down", "stale", "blind", "edge-1", "ghost")),
            candidate("skip", eligible=False),
        )
        plan = plan_waves(snapshot, rules, AT)
        self.assertEqual([w["device_ids"] for w in plan["waves"]], [["ok"]])
        self.assertEqual(reasons(plan), {
            "down": "down_candidate",
            "stale": "stale_candidate",
            "blind": "missing_evidence",
            "edge-1": "alternate_path_unavailable",
            "ghost": "invalid_wave",
            "skip": "not_eligible",
        })
        for emitted in plan["waves"]:
            self.assertEqual(validate_wave(snapshot, emitted, AT), ALLOWED)

    def test_canary_that_cannot_run_blocks_later_waves(self):
        snapshot = leaf_site("a", "b", "canary")
        rules = policy(candidate("a"), candidate("b"), candidate("canary", eligible=False), canary_id="canary")
        plan = plan_waves(snapshot, rules, AT)
        self.assertEqual(plan["waves"], [])
        self.assertEqual(plan["deferred"], [
            {"id": "canary", "reason": "not_eligible"},
            {"id": "a", "reason": "canary_not_scheduled"},
            {"id": "b", "reason": "canary_not_scheduled"},
        ])

        rules["candidates"][2]["eligible"] = True
        snapshot["devices"]["canary"]["healthy"] = False
        self.assertEqual(reasons(plan_waves(snapshot, rules, AT)), {
            "canary": "down_candidate",
            "a": "canary_not_scheduled",
            "b": "canary_not_scheduled",
        })

    def test_invalid_configuration_emits_no_waves(self):
        good = policy(candidate("a"), candidate("b"))
        cases = {
            "unknown canary": dict(good, canary_id="zzz"),
            "duplicate candidate": dict(good, candidates=[candidate("a"), candidate("a"), candidate("b")]),
            "missing blackouts": {k: v for k, v in good.items() if k != "blackouts"},
            "zero duration": dict(good, duration_seconds=0),
            "boolean criticality": dict(good, candidates=[candidate("a", criticality=True), candidate("b")]),
            "naive deadline": dict(good, candidates=[candidate("a", deadline="2026-11-12T00:00:00"), candidate("b")]),
            "inverted window": dict(good, windows=[
                {"starts_at": "2026-11-10T22:00:00Z", "ends_at": "2026-11-10T20:00:00Z"},
            ]),
        }
        bad_snapshot = leaf_site("a", "b")
        bad_snapshot["revision"] = 0
        runs = [(name, leaf_site("a", "b"), rules, AT) for name, rules in cases.items()]
        runs += [
            ("invalid snapshot", bad_snapshot, good, AT),
            ("naive evaluation time", leaf_site("a", "b"), good, "2026-11-10T18:00:00"),
        ]
        expected = [{"id": "a", "reason": "invalid_configuration"}, {"id": "b", "reason": "invalid_configuration"}]
        for name, snapshot, rules, at in runs:
            with self.subTest(name):
                plan = plan_waves(snapshot, rules, at)
                self.assertEqual((plan["waves"], plan["deferred"]), ([], expected))

    def test_random_plans_schedule_exactly_the_oracle_safe_candidates(self):
        rng = random.Random(7)
        for _ in range(100):
            snapshot = random_site(rng)
            plan = plan_waves(snapshot, policy(*(candidate(d) for d in snapshot["devices"])), AT)
            expected = sorted(
                d for d, e in snapshot["devices"].items()
                if e["healthy"] and oracle_keeps_services_reachable(snapshot, {d})
            )
            self.assertEqual(sorted(w["device_ids"][0] for w in plan["waves"]), expected, snapshot)
            for emitted in plan["waves"]:
                self.assertEqual(validate_wave(snapshot, emitted, AT), ALLOWED)


if __name__ == "__main__":
    unittest.main()
