#!/usr/bin/env bash
set -euo pipefail

test "$(kubectl config current-context)" = kind-kind
current=$(kubectl -n fleet get deployment api -o jsonpath='{.spec.template.spec.containers[0].image}')
test "$current" = fleet-api:smoke
postgres() { kubectl -n fleet exec -i deployment/postgres -- "$@"; }
fingerprint() {
  postgres psql -U fleet -d "$1" -v ON_ERROR_STOP=1 -Atc "
    SELECT jsonb_build_array(
      (SELECT jsonb_agg(version ORDER BY version) FROM inventory_migrations),
      (SELECT count(*) FROM assets),
      (SELECT md5(jsonb_agg(graph ORDER BY tenant_id, site_id, revision)::text) FROM rehearsal_snapshots),
      (SELECT count(*) FROM rehearsal_audit),
      (SELECT count(*) FROM observation_events),
      (SELECT snapshot_revision FROM rehearsal_sites WHERE tenant_id='northstar' AND site_id='harbor')
    )"
}

snapshot=$(fingerprint fleet)
postgres pg_dump -U fleet -Fc fleet > "$RUNNER_TEMP/fleet-recovery.dump"
restore_start=$SECONDS
postgres createdb -U fleet fleet_restore
postgres pg_restore -U fleet --exit-on-error -d fleet_restore < "$RUNNER_TEMP/fleet-recovery.dump"
test "$(fingerprint fleet_restore)" = "$snapshot"
restore_seconds=$((SECONDS - restore_start))

verify_api() {
  kubectl -n fleet rollout status deployment/api --timeout=180s
  kubectl -n fleet exec deployment/web -- wget -qO- http://127.0.0.1:8080/api/healthz
}
rollback_start=$SECONDS
kubectl -n fleet set image deployment/api api=fleet-api:previous
verify_api
rollback_seconds=$((SECONDS - rollback_start))
kubectl -n fleet set image deployment/api "api=$current"
verify_api

kubectl -n fleet delete pod -l app=fleet-worker
kubectl -n fleet rollout status deployment/worker --timeout=180s
test "$(fingerprint fleet)" = "$snapshot"
mkdir -p reports
jq -n --argjson restore "$restore_seconds" --argjson rollback "$rollback_seconds" \
  --argjson fingerprint "$snapshot" --arg source "$(git rev-parse HEAD)" \
  --arg previous "$(git -C previous-release rev-parse HEAD)" \
  '{environment:"disposable_kind",source:$source,previous_source:$previous,restore_wall_seconds:$restore,
    rollback_wall_seconds:$rollback,restored_state:$fingerprint,returned_to_candidate:true,worker_restarted:true}' \
  > reports/local-recovery.json
