"""Measure the authenticated first inventory page against a disposable PostgreSQL database."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPException
import json
from math import ceil
import os
from pathlib import Path
import platform
from queue import Empty, Queue
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import perf_counter, sleep
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


ASSETS = 10_000
RATE = 25
WARMUP_SECONDS = 5
TARGET_MS = 300
PAGE = "/v1/assets?tenant_id=benchmark&limit=25"
FIRST_PAGE = [f"asset-{n:05}" for n in range(25)]


def write_fixture(path):
    sites = [f"site-{n:02}" for n in range(10)]
    path.write_text(json.dumps({
        "tenants": [["benchmark", "Synthetic benchmark"]],
        "sites": [["benchmark", site, site] for site in sites],
        "principals": [["benchmark-operator", "Synthetic operator"]],
        "grants": [["benchmark-operator", "benchmark", site, "operator"] for site in sites],
        "assets": [],
    }), encoding="utf-8")


def seed_assets(db):
    db.execute("""WITH fixture AS (
        SELECT n, 'lab-version' AS running_release,
            (ARRAY['affected','affected','unaffected','unknown'])[(n %% 4) + 1] AS release_status,
            TRUE AS trunk_port, FALSE AS trustsec, FALSE AS macsec,
            '2026-11-10T18:00:00Z'::timestamptz AS observed_at
        FROM generate_series(0, %s) AS n
    )
    INSERT INTO assets (
        tenant_id, id, site_id, name, product, running_release, release_status,
        trunk_port, trustsec, macsec, observed_at, raw_evidence
    )
    SELECT 'benchmark', 'asset-' || lpad(n::text, 5, '0'), 'site-' || lpad((n %% 10)::text, 2, '0'),
        'Synthetic asset ' || n, 'Catalyst 9300', running_release, release_status,
        trunk_port, trustsec, macsec, observed_at, to_jsonb(fixture) - 'n'
    FROM fixture""", (ASSETS - 1,))
    db.execute("ANALYZE")


@contextmanager
def run_api(dsn, fixture):
    env = dict(os.environ, DATABASE_URL=dsn, FLEET_DEMO_MODE="local", FLEET_FIXTURE=str(fixture))
    with subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "fleet.main:app", "--host", "127.0.0.1", "--port", "0",
         "--no-access-log", "--no-use-colors", "--log-level", "info"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    ) as server:
        startup = Queue()

        def read_startup():
            for line in server.stderr:
                match = re.search(r"Uvicorn running on http://127\.0\.0\.1:(\d+)", line)
                if match:
                    startup.put(int(match[1]))
            startup.put(None)

        reader = Thread(target=read_startup, daemon=True)
        reader.start()
        try:
            try:
                port = startup.get(timeout=90)
            except Empty:
                raise RuntimeError("The benchmark API timed out while preparing the database.") from None
            if port is None:
                raise RuntimeError("The benchmark API exited before startup. Check API dependencies and database access.")
            yield port
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            reader.join(timeout=2)


def session_token(port):
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", "/v1/demo/session", json.dumps({"principal_id": "benchmark-operator"}),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"The synthetic operator session returned HTTP {response.status}.")
        return json.loads(response.read())["access_token"]
    finally:
        connection.close()


def request_page(port, token):
    start = perf_counter()
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", PAGE, headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        body = response.read()
        elapsed = (perf_counter() - start) * 1000
        if response.status != 200:
            return None, start, f"HTTP {response.status}"
        payload = json.loads(body)
        if (payload["total"] != ASSETS or payload["next_cursor"] != FIRST_PAGE[-1]
                or [item["id"] for item in payload["items"]] != FIRST_PAGE):
            return None, start, "Unexpected inventory page"
        return elapsed, start, None
    except (OSError, HTTPException, ValueError, KeyError, TypeError) as error:
        return None, start, type(error).__name__
    finally:
        connection.close()


def measure(port, token, seconds):
    samples = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        epoch = perf_counter()
        for index in range(RATE * seconds):
            scheduled = epoch + index / RATE
            sleep(max(0, scheduled - perf_counter()))
            samples.append((scheduled, pool.submit(request_page, port, token)))
        sleep(max(0, epoch + seconds - perf_counter()))
        return [(target, *sample.result()) for target, sample in samples]


def p95(values):
    ordered = sorted(values)
    return round(ordered[ceil(len(ordered) * 0.95) - 1], 3) if ordered else None


def benchmark(admin_dsn, seconds, source):
    database = f"fleet_benchmark_{uuid4().hex}"
    for key in list(os.environ):
        if key.startswith("PG"):
            del os.environ[key]
    with psycopg.connect(admin_dsn, connect_timeout=5, autocommit=True) as admin:
        postgres = admin.execute("SELECT version()").fetchone()[0]
        shared_buffers = admin.execute("SHOW shared_buffers").fetchone()[0]
        admin.execute(sql.SQL("CREATE DATABASE {} WITH TEMPLATE template0").format(sql.Identifier(database)))
        try:
            dsn = make_conninfo(admin_dsn, dbname=database, connect_timeout=5)
            with TemporaryDirectory() as directory:
                fixture = Path(directory) / "inventory.json"
                write_fixture(fixture)
                with run_api(dsn, fixture) as port:
                    with psycopg.connect(dsn, autocommit=True) as db:
                        seed_assets(db)
                    token = session_token(port)
                    warmup = measure(port, token, WARMUP_SECONDS)
                    warmup_errors = Counter(error for _, _, _, error in warmup if error)
                    samples = measure(port, token, seconds) if not warmup_errors else []
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))

    errors = Counter(error for _, _, _, error in samples if error)
    latencies = [latency for _, latency, _, _ in samples if latency is not None]
    lags = [(start - target) * 1000 for target, _, start, _ in samples]
    lag = p95(lags)
    valid = not errors and lag is not None and lag < 1000 / RATE
    dispatch_span = samples[-1][2] - samples[0][2] if samples else None
    latency = p95(latencies)
    return {
        "source_sha": source,
        "scope": "Synthetic operator with all 10 sites in one tenant",
        "assets": ASSETS,
        "endpoint": PAGE,
        "requests_per_second": RATE,
        "warmup_seconds": WARMUP_SECONDS,
        "measurement_seconds": seconds,
        "requests_attempted": len(samples),
        "requests_succeeded": len(latencies),
        "observed_requests_per_second": round((len(samples) - 1) / dispatch_span, 3) if dispatch_span else None,
        "warmup_errors": dict(warmup_errors),
        "errors": dict(errors),
        "p95_dispatch_lag_ms": lag,
        "max_dispatch_lag_ms": round(max(lags), 3) if lags else None,
        "p95_response_ms": latency,
        "target_p95_ms_exclusive": TARGET_MS,
        "valid_workload": valid,
        "target_met": valid and latency < TARGET_MS,
        "environment": {
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "postgres": postgres,
            "postgres_shared_buffers": shared_buffers,
            "python": platform.python_version(),
            "api_processes": 1,
            "transport": "Loopback HTTP, a new connection per request, no TLS",
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-dsn", required=True, help="A trusted disposable PostgreSQL server.")
    parser.add_argument("--seconds", type=int, default=20, help="Measurement duration from 10 to 120 seconds.")
    parser.add_argument("--source", help="Exact source commit SHA for the report.")
    args = parser.parse_args()
    loopback = re.fullmatch(r"postgresql://fleet@127\.0\.0\.1:([1-9][0-9]{0,4})/postgres", args.admin_dsn)
    compose = args.admin_dsn == "postgresql://fleet@db:5432/postgres" and Path("/.dockerenv").is_file()
    if not (compose or (loopback and int(loopback[1]) <= 65535)):
        parser.error("Use the documented local benchmark DSN or the disposable Compose database.")
    if not 10 <= args.seconds <= 120:
        parser.error("--seconds must be between 10 and 120.")
    if args.source and not re.fullmatch(r"[a-f0-9]{40}", args.source):
        parser.error("--source must be a 40-character commit SHA.")
    report = benchmark(args.admin_dsn, args.seconds, args.source)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["valid_workload"] else 1)
