"""Compare the actual planner with severity-first on reproducible synthetic fleets."""

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

from fleet.planner import plan_waves, validate_wave, validate_wave_policy


DURATION, MAX_AGE, HORIZON = 5, 150, 180
WINDOWS = ((0, 35), (45, 80), (120, 170))
BLACKOUT = (12, 20)
EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)


def stamp(minute):
    return (EPOCH + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")


def interval(start, end):
    return {"starts_at": stamp(start), "ends_at": stamp(end)}


def workload(seed):
    rng = random.Random(seed)
    for n in range(6):
        devices = {"canary": dict(priority=0, deadline=35, affected=True, eligible=True, observed=-1)}
        pairs = [(f"link-{i}-a", f"link-{i}-b") for i in range(3)]
        for left, right in pairs:
            for name in (left, right):
                devices[name] = dict(priority=rng.randint(1, 5), deadline=rng.choice([35, 80, 170]),
                                     affected=rng.random() < 0.8, eligible=rng.random() < 0.9, observed=-1)
        devices["link-0-a"].update(affected=False)
        devices["link-0-b"].update(affected=True, eligible=True, priority=5, deadline=35)
        devices["link-1-a"].update(affected=True, eligible=True, observed=-151)
        yield f"site-{n}", devices, pairs, ("link-0-a", 1 + rng.randrange(4))


def fits(start, deadline):
    end = start + DURATION
    return (end <= deadline and any(left <= start < end <= right for left, right in WINDOWS)
            and (end <= BLACKOUT[0] or start >= BLACKOUT[1]))


def baseline(devices, canary_first=False):
    cursor, waves, missed = 0, [], {}
    for name, device in sorted(devices.items(), key=lambda item: (
            canary_first and item[0] != "canary", -item[1]["priority"], item[1]["deadline"], item[0])):
        if not device["affected"] or not device["eligible"]:
            continue
        start = next((minute for minute in range(cursor, device["deadline"]) if fits(minute, device["deadline"])), None)
        if start is None:
            missed[name] = "no_window_before_deadline"
        else:
            waves.append((name, start))
            cursor = start + DURATION
    return waves, missed


def snapshot(site, devices, pairs, outage=None):
    health = {name: {"healthy": True, "observed_at": stamp(device["observed"])} for name, device in devices.items()}
    if outage:
        health[outage[0]] = {"healthy": False, "observed_at": stamp(outage[1])}
    services = {f"service-{idx}": f"edge-{idx}" for idx in range(len(pairs))}
    for edge in services.values():
        health[edge] = {"healthy": True, "observed_at": stamp(0)}
    return {"site_id": site, "revision": 2 if outage else 1, "max_age_seconds": MAX_AGE * 60,
            "devices": health, "links": [(edge, uplink) for edge, pair in zip(services.values(), pairs, strict=True)
                                        for uplink in pair],
            "services": services, "uplinks": [uplink for pair in pairs for uplink in pair]}


def policy(devices):
    return {"canary_id": "canary", "duration_seconds": DURATION * 60,
            "windows": [interval(*window) for window in WINDOWS], "blackouts": [interval(*BLACKOUT)],
            "candidates": [{"id": name, "eligible": device["eligible"] and device["affected"],
                            "deadline": stamp(device["deadline"]), "criticality": device["priority"]}
                           for name, device in sorted(devices.items())]}


def oracle(devices, pairs, name, at, outage):
    down = outage[0] if outage and at >= outage[1] else None
    if at - devices[name]["observed"] > MAX_AGE:
        return "stale_candidate"
    if name == down:
        return "down_candidate"
    if at > MAX_AGE or any(all(link in (name, down) or at - devices[link]["observed"] > MAX_AGE
                                for link in pair) for pair in pairs):
        return "alternate_path_unavailable"
    return None


def evaluate(site, devices, pairs, outage, waves, deferred):
    guard_policy, proposed, completed = policy(devices), set(), {}
    result, reasons = Counter(), Counter(deferred.values())
    cursor = 0
    for name, start in waves:
        end = start + DURATION
        device = devices[name]
        assert name not in proposed and start >= cursor and fits(start, device["deadline"])
        assert device["affected"] and device["eligible"]
        if oracle(devices, pairs, name, 0, None) or (name != "canary" and "canary" not in proposed):
            result["invalid_proposals"] += 1
        proposed.add(name)
        cursor = end
        wave = {**interval(start, end), "device_ids": [name]}
        runtime = snapshot(site, devices, pairs, outage if start >= outage[1] else None)
        actual = validate_wave_policy(guard_policy, wave, completed) or validate_wave(runtime, wave, stamp(start))["reason"]
        expected = ("prerequisite_incomplete" if name != "canary" and "canary" not in completed
                    else oracle(devices, pairs, name, start, outage))
        if expected:
            result["unsafe_start_attempts"] += 1
        if actual:
            result["blocked_starts"] += 1
            reasons[actual] += 1
        if expected and not actual:
            result["unsafe_starts_admitted"] += 1
        assert actual == expected, (site, name, start, actual, expected)
        if not actual:
            completed[name] = end
    result["proposed_waves"] = len(proposed)
    result["finished_by_deadline"] = sum(completed.get(name, HORIZON + 1) <= device["deadline"]
                                         for name, device in devices.items() if device["affected"] and device["eligible"])
    result["eligible_affected_devices"] = sum(device["affected"] and device["eligible"] for device in devices.values())
    result["affected_device_minutes"] = sum(completed.get(name, HORIZON)
                                            for name, device in devices.items() if device["affected"])
    return result, reasons


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("seeds", type=int, nargs="*", default=[7, 19, 43, 101, 209])
    seeds = parser.parse_args().seeds
    results = []
    for seed in seeds:
        initial = list(workload(seed))
        assert initial == list(workload(seed))
        totals = {name: (Counter(), Counter()) for name in ("severity_first", "severity_with_canary", "fleet_rehearsal")}
        for site, devices, pairs, outage in initial:
            assert oracle(devices, pairs, "link-0-b", 0, None) is None
            assert oracle(devices, pairs, "link-0-b", outage[1], outage) == "alternate_path_unavailable"
            plan = plan_waves(snapshot(site, devices, pairs), policy(devices), stamp(0))
            aware = [(wave["device_ids"][0], int((datetime.fromisoformat(wave["starts_at"]) - EPOCH).total_seconds() / 60))
                     for wave in plan["waves"]]
            for label, (waves, deferred) in (("severity_first", baseline(devices)),
                    ("severity_with_canary", baseline(devices, canary_first=True)),
                    ("fleet_rehearsal", (aware, {row["id"]: row["reason"] for row in plan["deferred"]}))):
                metrics, reasons = evaluate(site, devices, pairs, outage, waves, deferred)
                totals[label][0].update(metrics)
                totals[label][1].update(reasons)
        row = {"seed": seed}
        for name, (totals_by_metric, reasons) in totals.items():
            for key in ("invalid_proposals", "unsafe_start_attempts", "blocked_starts", "unsafe_starts_admitted"):
                totals_by_metric.setdefault(key, 0)
            row[name] = {**totals_by_metric, "affected_device_hours": round(totals_by_metric["affected_device_minutes"] / 60, 3),
                         "deferral_and_block_reasons": reasons}
        assert row["fleet_rehearsal"]["unsafe_starts_admitted"] == 0
        assert row["fleet_rehearsal"]["invalid_proposals"] == 0
        assert row["fleet_rehearsal"]["finished_by_deadline"] > 0
        results.append(row)
    print(json.dumps({"sites_per_seed": 6, "assets_per_site": 7, "horizon_minutes": HORIZON, "runs": results}, indent=2))


if __name__ == "__main__":
    main()
