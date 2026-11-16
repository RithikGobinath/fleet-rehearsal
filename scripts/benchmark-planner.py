"""Compare two canary-first schedules through the same production wave validators."""

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

from fleet.planner import plan_waves, validate_wave, validate_wave_policy


DURATION, MAX_AGE, HORIZON = 5, 150, 180
CASES_PER_SEED = 6
PLANNERS = ("severity_with_canary", "fleet_rehearsal")
WINDOWS = ((0, 35), (45, 80), (120, 170))
BLACKOUT = (12, 20)
EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)


def stamp(minute):
    return (EPOCH + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")


def interval(start, end):
    return {"starts_at": stamp(start), "ends_at": stamp(end)}


def workload(seed):
    for n in range(CASES_PER_SEED):
        rng = random.Random(f"{seed}:{n}")
        devices = {"canary": dict(priority=0, deadline=35, affected=True, eligible=True, observed=-1)}
        pairs = [(f"link-{i}-a", f"link-{i}-b") for i in range(3)]
        for left, right in pairs:
            for name in (left, right):
                devices[name] = dict(priority=rng.randint(1, 5), deadline=rng.choice([35, 80, 170]),
                                     affected=rng.random() < 0.8, eligible=rng.random() < 0.9,
                                     observed=-rng.randrange(161))
        outage = (rng.choice([link for pair in pairs for link in pair]), rng.randrange(1, 36)) if rng.random() < 0.5 else None
        yield f"site-{n}", devices, pairs, outage


def fits(start, deadline):
    end = start + DURATION
    return (end <= deadline and any(left <= start < end <= right for left, right in WINDOWS)
            and (end <= BLACKOUT[0] or start >= BLACKOUT[1]))


def baseline(devices):
    cursor, waves, missed = 0, [], {}
    for name, device in sorted(devices.items(), key=lambda item: (
            item[0] != "canary", -item[1]["priority"], item[1]["deadline"], item[0])):
        if not device["affected"] or not device["eligible"]:
            missed[name] = "not_eligible"
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


def expected_refusal(devices, pairs, name, at, outage):
    """Cross-check the shared policy for the benchmark's redundant pairs."""
    down = outage[0] if outage and at >= outage[1] else None
    last_observed = outage[1] if name == down else devices[name]["observed"]
    if at - last_observed > MAX_AGE:
        return "stale_candidate"
    if name == down:
        return "down_candidate"
    if at > MAX_AGE or any(all(link in (name, down) or at - devices[link]["observed"] > MAX_AGE
                                for link in pair) for pair in pairs):
        return "alternate_path_unavailable"
    return None


def evaluate(site, devices, pairs, outage, waves, deferred):
    guard_policy, proposed, completed = policy(devices), set(), {}
    result, blocks, proposals = Counter(), Counter(), Counter()
    cursor = 0
    for name, start in waves:
        end = start + DURATION
        device = devices[name]
        assert name not in proposed and start >= cursor and fits(start, device["deadline"])
        assert device["affected"] and device["eligible"]
        assert name == "canary" or "canary" in proposed
        preview_reason = expected_refusal(devices, pairs, name, 0, None)
        if preview_reason:
            result["proposals_rejected_at_preview"] += 1
            proposals[preview_reason] += 1
        proposed.add(name)
        cursor = end
        wave = {**interval(start, end), "device_ids": [name]}
        runtime = snapshot(site, devices, pairs, outage if outage and start >= outage[1] else None)
        actual = validate_wave_policy(guard_policy, wave, completed) or validate_wave(runtime, wave, stamp(start))["reason"]
        expected = ("prerequisite_incomplete" if name != "canary" and "canary" not in completed
                    else expected_refusal(devices, pairs, name, start, outage))
        if expected:
            result["unsafe_start_attempts"] += 1
        if actual:
            result["blocked_starts"] += 1
            blocks[actual] += 1
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
    return result, proposals, blocks, Counter(deferred.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("seeds", type=int, nargs="*", default=[7, 19, 43, 101, 209])
    seeds = parser.parse_args().seeds
    results = []
    for seed in seeds:
        initial = list(workload(seed))
        assert initial == list(workload(seed))
        totals = {name: (Counter(), Counter(), Counter(), Counter()) for name in PLANNERS}
        for site, devices, pairs, outage in initial:
            plan = plan_waves(snapshot(site, devices, pairs), policy(devices), stamp(0))
            aware = [(wave["device_ids"][0], int((datetime.fromisoformat(wave["starts_at"]) - EPOCH).total_seconds() / 60))
                     for wave in plan["waves"]]
            for label, (waves, deferred) in (("severity_with_canary", baseline(devices)),
                    ("fleet_rehearsal", (aware, {row["id"]: row["reason"] for row in plan["deferred"]}))):
                for total, counts in zip(totals[label], evaluate(site, devices, pairs, outage, waves, deferred), strict=True):
                    total.update(counts)
        row = {"seed": seed}
        for name, (totals_by_metric, proposals, blocks, deferred) in totals.items():
            for key in ("proposals_rejected_at_preview", "unsafe_start_attempts", "blocked_starts", "unsafe_starts_admitted"):
                totals_by_metric.setdefault(key, 0)
            assert totals_by_metric["unsafe_starts_admitted"] == 0
            row[name] = {**totals_by_metric, "affected_device_hours": round(totals_by_metric["affected_device_minutes"] / 60, 3),
                         "preview_refusal_reasons": proposals, "blocked_start_reasons": blocks, "deferral_reasons": deferred}
        assert row["fleet_rehearsal"]["proposals_rejected_at_preview"] == 0
        results.append(row)
    print(json.dumps({
        "sites_per_seed": CASES_PER_SEED, "candidates_per_site": 7, "horizon_minutes": HORIZON,
        "workload": "One topology template. Seeded link observation ages (0–160 minutes) and optional outages.",
        "runs": results,
    }, indent=2))


if __name__ == "__main__":
    main()
