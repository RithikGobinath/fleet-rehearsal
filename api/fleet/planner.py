"""Pure maintenance wave planner and independent wave validator.

Both entry points take and return JSON-compatible dicts and use only the
standard library. Times are UTC ISO 8601 strings ("...Z" or "+00:00"), and
every interval is half-open: [starts_at, ends_at). Invalid configuration and
missing evidence fail closed: the validator rejects and the planner emits no
wave.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

# Validator reasons, in the order they are checked.
INVALID_CONFIGURATION = "invalid_configuration"
INVALID_WAVE = "invalid_wave"
MISSING_EVIDENCE = "missing_evidence"
STALE_CANDIDATE = "stale_candidate"
DOWN_CANDIDATE = "down_candidate"
ALTERNATE_PATH_UNAVAILABLE = "alternate_path_unavailable"

# Planner-only deferral reasons. Deferrals also reuse the validator reasons.
NOT_ELIGIBLE = "not_eligible"
DEADLINE_MISSED = "deadline_missed"
NO_WINDOW_BEFORE_DEADLINE = "no_window_before_deadline"
CANARY_NOT_SCHEDULED = "canary_not_scheduled"


class _Invalid(ValueError):
    """Input breaks the contract."""


def validate_wave(snapshot, wave, at):
    """Decide whether a wave may run, independently of how it was planned.

    Wave devices and every device that is down, stale or missing evidence at
    `at` are removed from the topology. Every service attachment must then
    still reach a remaining uplink. Returns {"allowed": bool, "reason": str
    or None}.
    """
    try:
        devices, links, uplinks, services, max_age = _read_snapshot(snapshot)
        now = _utc(at)
    except _Invalid:
        return _rejected(INVALID_CONFIGURATION)
    try:
        wave_ids = _read_wave(wave, devices, now)
    except _Invalid:
        return _rejected(INVALID_WAVE)

    problems = {_evidence_problem(devices[d], now, max_age) for d in wave_ids}
    for reason in (MISSING_EVIDENCE, STALE_CANDIDATE, DOWN_CANDIDATE):
        if reason in problems:
            return _rejected(reason)

    available = {
        device_id
        for device_id, device in devices.items()
        if device_id not in wave_ids and _evidence_problem(device, now, max_age) is None
    }
    reachable = _reachable_from_uplinks(available, links, uplinks)
    if not all(attachment in reachable for attachment in services.values()):
        return _rejected(ALTERNATE_PATH_UNAVAILABLE)
    return {"allowed": True, "reason": None}


def plan_waves(snapshot, policy, at):
    """Plan sequential single-device waves for the snapshot's site.

    Candidates are ordered by deadline, descending criticality, then ID, with
    a nominated canary first. If the canary cannot be scheduled, every other
    candidate is deferred. Each wave takes the earliest `duration_seconds`
    slot that starts no earlier than `at` and the previous wave's end, lies
    inside one window (overlapping or touching windows merge), avoids every
    blackout and ends by the candidate's deadline. Health is judged once, at
    `at`, and a wave is emitted only if validate_wave allows it.
    """
    try:
        snapshot = normalize_snapshot(snapshot)
        now = _utc(at)
        candidates, windows, blackouts, duration, canary_id = _read_policy(policy)
    except _Invalid:
        deferred = [{"id": cid, "reason": INVALID_CONFIGURATION} for cid in _readable_ids(policy)]
        return _plan(None, None, [], deferred)

    queue = sorted(candidates, key=lambda c: (c["deadline"], -c["criticality"], c["id"]))
    queue.sort(key=lambda c: c["id"] != canary_id)  # stable: the canary, if any, moves first

    waves, deferred = [], []
    cursor = now
    for candidate in queue:
        if canary_id is not None and candidate["id"] != canary_id and not waves:
            reason, slot = CANARY_NOT_SCHEDULED, None
        else:
            reason, slot = _fit(candidate, now, cursor, duration, windows, blackouts)
        if slot is not None:
            wave = {
                "device_ids": [candidate["id"]],
                "starts_at": _format_utc(slot[0]),
                "ends_at": _format_utc(slot[1]),
                "canary": candidate["id"] == canary_id,
            }
            verdict = validate_wave(snapshot, wave, at)
            if verdict["allowed"]:
                waves.append(wave)
                cursor = slot[1]
                continue
            reason = verdict["reason"]
        deferred.append({"id": candidate["id"], "reason": reason})
    return _plan(snapshot["site_id"], snapshot["revision"], waves, deferred)


def _fit(candidate, now, cursor, duration, windows, blackouts):
    """Return (None, (start, end)) for the earliest allowed slot, or (reason, None)."""
    if not candidate["eligible"]:
        return NOT_ELIGIBLE, None
    deadline = candidate["deadline"]
    if now + duration > deadline:
        return DEADLINE_MISSED, None
    for window_start, window_end in windows:
        start = max(window_start, cursor)
        while start + duration <= min(window_end, deadline):
            end = start + duration
            clashes = [b_end for b_start, b_end in blackouts if b_start < end and start < b_end]
            if not clashes:
                return None, (start, end)
            start = max(clashes)
    return NO_WINDOW_BEFORE_DEADLINE, None


def _reachable_from_uplinks(available, links, uplinks):
    neighbours = {device_id: set() for device_id in available}
    for a, b in links:
        if a in available and b in available:
            neighbours[a].add(b)
            neighbours[b].add(a)
    reached = {uplink for uplink in uplinks if uplink in available}
    frontier = list(reached)
    while frontier:
        for neighbour in neighbours[frontier.pop()] - reached:
            reached.add(neighbour)
            frontier.append(neighbour)
    return reached


def _evidence_problem(device, now, max_age):
    """Return why a device cannot count as available at `now`, or None."""
    healthy = device.get("healthy")
    observed_at = _parse_utc(device.get("observed_at"))
    if not isinstance(healthy, bool) or observed_at is None:
        return MISSING_EVIDENCE
    if not 0 <= (now - observed_at).total_seconds() <= max_age:
        return STALE_CANDIDATE
    if not healthy:
        return DOWN_CANDIDATE
    return None


def normalize_snapshot(snapshot):
    """Return one canonical, site-local graph; unusable evidence remains unknown."""
    devices, links, uplinks, services, max_age = _read_snapshot(snapshot)
    normalized = {}
    for device_id, device in sorted(devices.items()):
        healthy = device.get("healthy")
        observed_at = _parse_utc(device.get("observed_at"))
        normalized[device_id] = {
            "healthy": healthy if isinstance(healthy, bool) else None,
            "observed_at": _format_utc(observed_at) if observed_at is not None else None,
            "redundancy_group": device.get("redundancy_group"),
        }
    return {
        "site_id": snapshot["site_id"],
        "revision": snapshot["revision"],
        "max_age_seconds": max_age,
        "devices": normalized,
        "links": [list(edge) for edge in sorted({tuple(sorted(link)) for link in links})],
        "uplinks": sorted(set(uplinks)),
        "services": dict(sorted(services.items())),
    }


def _read_snapshot(snapshot):
    """Return (devices, links, uplinks, services, max_age_seconds) or raise _Invalid."""
    _require(isinstance(snapshot, dict))
    _require(_is_id(snapshot.get("site_id")))
    revision = snapshot.get("revision")
    _require(_is_int(revision) and revision > 0)
    max_age = snapshot.get("max_age_seconds")
    _require(_is_int(max_age) and max_age > 0)
    devices = snapshot.get("devices")
    _require(isinstance(devices, dict))
    for device_id, device in devices.items():
        _require(_is_id(device_id) and isinstance(device, dict))
        group = device.get("redundancy_group")
        _require(group is None or _is_id(group))
    links = snapshot.get("links")
    _require(isinstance(links, list))
    for link in links:
        _require(isinstance(link, (list, tuple)) and len(link) == 2 and link[0] != link[1])
        _require(all(_is_known(end, devices) for end in link))
    uplinks = snapshot.get("uplinks")
    _require(isinstance(uplinks, list) and uplinks)
    _require(all(_is_known(uplink, devices) for uplink in uplinks))
    services = snapshot.get("services")
    _require(isinstance(services, dict) and services)
    _require(all(_is_id(s) and _is_known(d, devices) for s, d in services.items()))
    return devices, links, uplinks, services, max_age


def _read_wave(wave, devices, now):
    """Return the wave's device IDs as a set or raise _Invalid."""
    _require(isinstance(wave, dict))
    ids = wave.get("device_ids")
    _require(isinstance(ids, list) and ids and all(_is_known(d, devices) for d in ids))
    _require(len(set(ids)) == len(ids))
    starts_at, ends_at = _utc(wave.get("starts_at")), _utc(wave.get("ends_at"))
    _require(starts_at < ends_at and now < ends_at)
    groups = [devices[d].get("redundancy_group") for d in ids]
    groups = [group for group in groups if group is not None]
    _require(len(set(groups)) == len(groups))
    return set(ids)


def _read_policy(policy):
    """Return (candidates, windows, blackouts, duration, canary_id) or raise _Invalid."""
    _require(isinstance(policy, dict) and isinstance(policy.get("candidates"), list))
    candidates = []
    for raw in policy["candidates"]:
        _require(isinstance(raw, dict) and _is_id(raw.get("id")))
        _require(_is_int(raw.get("criticality")) and isinstance(raw.get("eligible"), bool))
        candidates.append({
            "id": raw["id"],
            "deadline": _utc(raw.get("deadline")),
            "criticality": raw["criticality"],
            "eligible": raw["eligible"],
        })
    ids = [c["id"] for c in candidates]
    _require(len(set(ids)) == len(ids))
    canary_id = policy.get("canary_id")
    _require(canary_id is None or canary_id in ids)
    duration = policy.get("duration_seconds")
    _require(_is_int(duration) and duration > 0)
    windows = _intervals(policy.get("windows"))
    blackouts = _intervals(policy.get("blackouts"))
    return candidates, windows, blackouts, timedelta(seconds=duration), canary_id


def _intervals(raw):
    """Parse [{starts_at, ends_at}] into sorted, merged half-open (start, end) pairs."""
    _require(isinstance(raw, list))
    parsed = []
    for item in raw:
        _require(isinstance(item, dict))
        start, end = _utc(item.get("starts_at")), _utc(item.get("ends_at"))
        _require(start < end)
        parsed.append((start, end))
    merged = []
    for start, end in sorted(parsed):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _readable_ids(policy):
    """Candidate IDs that can still be named when the configuration is invalid."""
    raw = policy.get("candidates") if isinstance(policy, dict) else None
    if not isinstance(raw, list):
        return []
    return sorted({c["id"] for c in raw if isinstance(c, dict) and _is_id(c.get("id"))})


def _plan(site_id, revision, waves, deferred):
    """Attach a SHA-256 fingerprint over the canonical plan and its snapshot identity."""
    canonical = json.dumps(
        {"site_id": site_id, "snapshot_revision": revision, "waves": waves, "deferred": deferred},
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"waves": waves, "deferred": deferred, "fingerprint": fingerprint}


def _rejected(reason):
    return {"allowed": False, "reason": reason}


def _require(condition):
    if not condition:
        raise _Invalid


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_id(value):
    return isinstance(value, str) and value != ""


def _is_known(value, devices):
    return isinstance(value, str) and value in devices


def _parse_utc(value):
    """Return an aware datetime for a UTC ISO 8601 string, otherwise None."""
    if not isinstance(value, str):
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(timezone.utc)


def _utc(value):
    parsed = _parse_utc(value)
    _require(parsed is not None)
    return parsed


def _format_utc(moment):
    return moment.isoformat().replace("+00:00", "Z")
