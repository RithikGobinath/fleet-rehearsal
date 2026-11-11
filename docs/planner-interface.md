# Planner interface

`plan_waves(snapshot, policy, at)` and `validate_wave(snapshot, wave, at)` are pure functions over JSON values. They use the supplied evaluation time, without I/O or a system clock. Inputs use UTC; intervals are half-open `[starts_at, ends_at)`.

| Input | Fields |
| --- | --- |
| Snapshot | `site_id`, positive `revision` and `max_age_seconds`, `devices` keyed by ID with `healthy`, `observed_at`, optional `redundancy_group`; undirected `links` as ID pairs, `uplinks` as IDs, and `services` mapping protected service IDs to attachment IDs. The caller supplies a single tenant's site. |
| Policy | `candidates` with unique `id`, `eligible`, UTC `deadline`, and integer `criticality`; `windows`, `blackouts`, positive `duration_seconds`, and optional `canary_id` naming a candidate. Each interval has `starts_at` and `ends_at`. |
| Wave | Nonempty unique `device_ids`, UTC `starts_at` and `ends_at`; output waves also include `canary`. The HTTP adapter adds `wave_number`. |

The validator returns `{"allowed": true, "reason": null}` or a denied verdict with one reason. It rejects malformed input or waves, including duplicate redundancy group members and waves already ended. Wave devices must have complete, healthy evidence with age in `[0, max_age_seconds]`. Missing, stale, future-dated, or down evidence never counts toward reachability. With the wave devices removed, every protected service must reach a remaining healthy uplink through usable nodes. An unusable peer leads to `alternate_path_unavailable` if it cuts the last path. The candidate reasons are `missing_evidence`, `stale_candidate`, and `down_candidate`.

The planner returns `{waves, deferred, fingerprint}`. It puts the canary first, then sorts by deadline ascending, criticality descending, and ID ascending. Each single-device wave takes the earliest nonoverlapping slot that starts at or after evaluation, fits an allowed window and the deadline, avoids blackouts, and passes validation. It evaluates health as of the supplied time, so a proposal is not a promise about future health. It returns deferrals as `{id, reason}` in processing order, with `invalid_configuration` for readable IDs when input configuration fails. When the canary cannot be scheduled it defers later candidates with `canary_not_scheduled`. Other scheduling reasons are `not_eligible`, `deadline_missed`, and `no_window_before_deadline`, plus validator reasons. The HTTP adapter maps deferred `id` to `asset_id`.

The pure fingerprint is the lowercase SHA-256 of compact, sorted-key JSON over site ID, snapshot revision, waves, and deferrals. The [HTTP fingerprint](first-slice-api.md) separately binds the tenant, plan and policy revision. Before reserving a run, the caller checks grants and exact approval under the site guard, compares policy and pending evidence, enforces the window and earlier wave outcomes, and calls the validator again with fresh evidence. The draft validator does not enforce these stateful checks.

In the [Harbor fixture](../fixtures/peer_down_after_approval.json), `dispatch` reaches both uplinks via `service-edge`. Wave 1 removes `harbor-01`; healthy `harbor-02` preserves the path. After approval, snapshot 2 reports `harbor-02` down. The same wave at 19:01Z returns `{"allowed": false, "reason": "alternate_path_unavailable"}`, which the planned HTTP API exposes as a blocked start.
