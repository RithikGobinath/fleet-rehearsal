import json
import os

import psycopg

from fleet.rehearsal import seed_rehearsal
from fleet.settings import database_config
from fleet.storage import initialize_inventory


def migrate(database_url, fixture_path):
    seed = json.loads(fixture_path.read_text(encoding="utf-8"))
    with psycopg.connect(database_url) as db, db.transaction():
        initialize_inventory(db, seed)
        seed_rehearsal(db)


if __name__ == "__main__":
    migrate(*database_config(os.environ))
