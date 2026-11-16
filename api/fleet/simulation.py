"""Deterministic image-upgrade and service-probe model for synthetic rehearsals."""

from collections import defaultdict
from datetime import datetime, timedelta
from hashlib import sha256


UPGRADE_SECONDS = range(45, 121)
BOOT_FAILURE_PERCENT = 10


def start_simulation(wave, clock_at, seed, services, fail_canary=False):
    """Freeze seeded results before releasing the site's lock."""
    devices = []
    failure_fixture = fail_canary and wave["canary"]
    for asset_id in wave["device_ids"]:
        draw = sha256(f"{seed}:{asset_id}".encode()).digest()
        upgrade_seconds = UPGRADE_SECONDS[int.from_bytes(draw[:4], "big") % len(UPGRADE_SECONDS)]
        failed_boot = int.from_bytes(draw[4:8], "big") % 100 < BOOT_FAILURE_PERCENT
        devices.append({
            "asset_id": asset_id,
            "upgrade_seconds": upgrade_seconds,
            "boot_succeeded": not (failed_boot or failure_fixture),
        })
    return {
        "seed": seed,
        "simulated_start_at": clock_at.isoformat(),
        "protected_services": sorted(services),
        "failure_fixture": failure_fixture,
        "devices": devices,
    }


def finish_simulation(simulation, graph, snapshot_revision, clock_at):
    """Advance the model through a parallel upgrade and probe current paths."""
    devices = simulation["devices"]
    elapsed = max(device["upgrade_seconds"] for device in devices)
    verified_at = max(datetime.fromisoformat(simulation["simulated_start_at"]) + timedelta(seconds=elapsed), clock_at)
    usable = {
        asset for asset, evidence in graph["devices"].items()
        if _fresh_and_healthy(evidence, verified_at, graph["max_age_seconds"])
    }
    failed_boots = {device["asset_id"] for device in devices if not device["boot_succeeded"]}
    wave_assets = {device["asset_id"] for device in devices}
    after_upgrade = usable - failed_boots
    unavailable_devices = sorted(wave_assets - after_upgrade)
    protected_services = set(simulation["protected_services"]) | graph["services"].keys()
    unreachable = _probe_services(graph, protected_services, after_upgrade)
    evidence = {
        "seed": simulation["seed"],
        "simulated_elapsed_seconds": elapsed,
        "verified_at": verified_at.isoformat(),
        "snapshot_revision": snapshot_revision,
        "unavailable_devices": unavailable_devices,
        "unreachable_services": unreachable,
    }
    if not unavailable_devices and not unreachable:
        return "succeeded", [dict(device, outcome="succeeded") for device in devices], evidence

    # Restoring the original images clears only simulated boot failures.
    after_rollback = _probe_services(graph, protected_services, usable)
    evidence["rollback_unreachable_services"] = after_rollback
    outcome = "failed" if after_rollback or not wave_assets.issubset(usable) else "rolled_back"
    results = [
        dict(device, outcome="rolled_back" if device["asset_id"] in usable else "failed")
        for device in devices
    ]
    return outcome, results, evidence


def _fresh_and_healthy(evidence, at, max_age_seconds):
    if evidence["healthy"] is not True or not evidence["observed_at"]:
        return False
    age_seconds = (at - datetime.fromisoformat(evidence["observed_at"])).total_seconds()
    return 0 <= age_seconds <= max_age_seconds


def _probe_services(graph, protected_services, usable):
    """Trace a path from each protected service to any usable uplink."""
    links = defaultdict(set)
    for left, right in graph["links"]:
        if left in usable and right in usable:
            links[left].add(right)
            links[right].add(left)
    uplinks = usable.intersection(graph["uplinks"])
    missing = []
    for service in sorted(protected_services):
        attachment = graph["services"].get(service)
        frontier = [attachment] if attachment in usable else []
        seen = set(frontier)
        while frontier:
            node = frontier.pop()
            if node in uplinks:
                break
            neighbors = links[node] - seen
            frontier.extend(neighbors)
            seen.update(neighbors)
        else:
            missing.append(service)
    return missing
