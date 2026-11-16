from contextlib import ExitStack
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from fleet import metrics
from fleet.main import app


class HttpRuntimeTests(TestCase):
    def test_pool_returns_503_when_full_and_accepts_requests_after_release(self):
        with TestClient(app) as client:
            pool = app.state.db_pool
            with patch.object(pool, "timeout", 0.05), ExitStack() as held:
                for _ in range(pool.max_size):
                    held.enter_context(pool.connection(timeout=5))
                self.assertEqual(client.get("/readyz").status_code, 503)
            self.assertEqual(client.get("/readyz").status_code, 200)

    def test_http_histogram_carries_cumulative_buckets(self):
        with TestClient(app) as client, patch.object(metrics, "_latency", {
            "2xx": [0.0, [0] * (len(metrics._buckets) + 1)],
            "other": [0.0, [0] * (len(metrics._buckets) + 1)],
        }):
            for seconds in (0.01, 0.25, 8):
                metrics.observe_request(200, seconds)
            text = client.get("/metrics").text
            prefix = 'fleet_http_request_duration_seconds'
            self.assertIn(f'# TYPE {prefix} histogram\n', text)
            self.assertIn(f'{prefix}_bucket{{outcome="2xx",le="0.005"}} 0\n', text)
            self.assertIn(f'{prefix}_bucket{{outcome="2xx",le="0.01"}} 1\n', text)
            self.assertIn(f'{prefix}_bucket{{outcome="2xx",le="0.3"}} 2\n', text)
            self.assertIn(f'{prefix}_bucket{{outcome="2xx",le="+Inf"}} 3\n', text)
            self.assertIn(f'{prefix}_count{{outcome="2xx"}} 3\n', text)
