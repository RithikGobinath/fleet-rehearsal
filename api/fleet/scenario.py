import argparse
import os
from copy import deepcopy

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from fleet.rehearsal import site_state
from fleet.settings import LocalSettings


def apply_scenario(db, scope, scenario):
    with db.transaction():
        db.execute("SELECT 1 FROM sites WHERE (tenant_id,id)=(%s,%s) FOR UPDATE", scope)
        current = site_state(db, scope)
        graph = deepcopy(current["graph"])
        revision = current["snapshot_revision"] + 1
        graph["revision"] = revision
        if scenario == "peer-down":
            graph["devices"]["harbor-02"].update(healthy=False, observed_at="2026-11-10T19:00:30Z")
            now = "2026-11-10T19:01:00Z"
        else:
            now = "2026-11-10T19:10:00Z" if scenario == "healthy-next" else "2026-11-10T19:00:00Z"
            for device in ("harbor-01", "harbor-02", "service-edge"):
                graph["devices"][device].update(healthy=True, observed_at=now)
        db.execute("INSERT INTO rehearsal_snapshots VALUES (%s,%s,%s,%s)", (*scope, revision, Jsonb(graph)))
        db.execute("""UPDATE rehearsal_sites SET snapshot_revision=%s,clock_at=%s,fail_canary=%s
                      WHERE (tenant_id,site_id)=(%s,%s)""", (revision, now, scenario == "canary-fail", *scope))
        return revision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=("healthy", "healthy-next", "peer-down", "canary-fail"))
    args = parser.parse_args()
    settings = LocalSettings.from_env(os.environ)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as db:
        revision = apply_scenario(db, ("northstar", "harbor"), args.scenario)
    print(f"Harbor synthetic snapshot {revision}: {args.scenario}")


if __name__ == "__main__":
    main()
