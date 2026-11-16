import unittest
from datetime import datetime, timedelta, timezone

from fleet.simulation import finish_simulation, start_simulation


START = datetime(2026, 11, 10, 19, tzinfo=timezone.utc)
WAVE = {"device_ids": ["harbor-01"], "canary": True}


def topology():
    return {
        "devices": {
            asset: {"healthy": True, "observed_at": START.isoformat()}
            for asset in ("harbor-01", "harbor-02", "relay", "edge")
        },
        "links": [["edge", "relay"], ["relay", "harbor-01"], ["relay", "harbor-02"]],
        "uplinks": ["harbor-01", "harbor-02"],
        "services": {"dispatch": "edge"},
        "max_age_seconds": 300,
    }


class SimulationTests(unittest.TestCase):
    def test_seed_freezes_device_durations_and_boot_results(self):
        wave = {"device_ids": ["harbor-01", "harbor-02"], "canary": False}
        graph = topology()
        plan = start_simulation(wave, START, 1, graph["services"])
        self.assertEqual(plan, start_simulation(wave, START, 1, graph["services"]))
        outcome, devices, evidence = finish_simulation(plan, graph, 1, START)
        self.assertEqual(outcome, "succeeded")
        self.assertEqual([device["upgrade_seconds"] for device in devices], [50, 70])
        self.assertEqual(evidence["simulated_elapsed_seconds"], 70)
        self.assertEqual(datetime.fromisoformat(evidence["verified_at"]), START + timedelta(seconds=70))
        self.assertTrue(all(device["boot_succeeded"] for device in devices))

        seeded_failure = start_simulation(WAVE, START, 10, graph["services"])
        outcome, devices, evidence = finish_simulation(seeded_failure, graph, 1, START)
        self.assertEqual(outcome, "rolled_back")
        self.assertFalse(devices[0]["boot_succeeded"])
        self.assertEqual(evidence["unavailable_devices"], ["harbor-01"])
        self.assertEqual(evidence["unreachable_services"], [])

    def test_postcheck_probes_latest_topology_and_rollback(self):
        graph = topology()
        plan = start_simulation(WAVE, START, 1, graph["services"], fail_canary=True)
        graph["devices"]["harbor-02"]["healthy"] = False
        outcome, _, evidence = finish_simulation(plan, graph, 2, START)
        self.assertEqual((outcome, evidence["snapshot_revision"]), ("rolled_back", 2))
        self.assertEqual(evidence["unreachable_services"], ["dispatch"])
        self.assertEqual(evidence["rollback_unreachable_services"], [])

        graph["links"].remove(["edge", "relay"])
        outcome, devices, evidence = finish_simulation(plan, graph, 3, START)
        self.assertEqual(outcome, "failed")
        self.assertEqual(devices[0]["outcome"], "rolled_back")
        self.assertEqual(evidence["rollback_unreachable_services"], ["dispatch"])

    def test_observed_device_failure_cannot_be_rolled_back(self):
        graph = topology()
        plan = start_simulation(WAVE, START, 1, graph["services"])
        graph["devices"]["harbor-01"]["healthy"] = False
        outcome, devices, evidence = finish_simulation(plan, graph, 2, START)
        self.assertEqual(outcome, "failed")
        self.assertTrue(devices[0]["boot_succeeded"])
        self.assertEqual(devices[0]["outcome"], "failed")
        self.assertEqual(evidence["unavailable_devices"], ["harbor-01"])
        self.assertEqual(evidence["rollback_unreachable_services"], [])

    def test_postcheck_rejects_missing_service_and_expired_health(self):
        graph = topology()
        graph["services"]["voice"] = "relay"
        plan = start_simulation(WAVE, START, 1, graph["services"])
        del graph["services"]["voice"]
        graph["devices"]["relay"]["observed_at"] = (START - timedelta(seconds=280)).isoformat()
        outcome, devices, evidence = finish_simulation(plan, graph, 2, START)
        self.assertEqual(outcome, "failed")
        self.assertTrue(devices[0]["boot_succeeded"])
        self.assertEqual(evidence["unavailable_devices"], [])
        self.assertEqual(evidence["unreachable_services"], ["dispatch", "voice"])
