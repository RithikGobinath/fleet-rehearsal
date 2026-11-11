import argparse
import os
import time

import psycopg
from psycopg.rows import dict_row

from fleet.rehearsal import process_one
from fleet.settings import LocalSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Commit at most one simulator step and exit.")
    args = parser.parse_args()
    settings = LocalSettings.from_env(os.environ)
    while True:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as db:
            changed = process_one(db)
        if args.once:
            return
        if not changed:
            time.sleep(0.25)


if __name__ == "__main__":
    main()
