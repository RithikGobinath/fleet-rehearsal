import argparse
import os
import time

import psycopg
from psycopg.rows import dict_row

from fleet.rehearsal import process_one
from fleet.settings import LocalSettings, runtime_settings
from fleet.storage import worker_heartbeat


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Commit at most one simulator step and exit.")
    parser.add_argument("--seed", type=int, help="Set a repeatable seed for one local test step.")
    args = parser.parse_args()
    settings = runtime_settings(os.environ)
    if args.seed is not None and (not args.once or not isinstance(settings, LocalSettings)
                                  or not 1 <= args.seed <= 2**32):
        parser.error("--seed requires --once, local mode and a value from 1 to 4294967296.")
    while True:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as db:
            changed = process_one(db, simulation_seed=args.seed)
            worker_heartbeat(db, "rehearsal")
        if args.once:
            return
        if not changed:
            time.sleep(0.25)


if __name__ == "__main__":
    main()
