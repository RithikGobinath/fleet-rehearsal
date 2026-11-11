import json
import os
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("FLEET_TEST_URL", "http://127.0.0.1:8000")


def request(path, *, token=None, payload=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urlopen(Request(BASE_URL + path, headers=headers, data=data), timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        with error:
            return error.code, json.load(error)


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tokens = {}
        for principal in ("demo-northstar", "demo-summit", "demo-partner"):
            status, body = request("/v1/demo/session", payload={"principal_id": principal})
            if status != 200:
                raise RuntimeError(f"Demo sign-in failed for {principal}: {status}")
            cls.tokens[principal] = body["access_token"]

    def test_unauthenticated_and_invalid_requests(self):
        self.assertEqual(request("/v1/assets?tenant_id=northstar")[0], 401)
        self.assertEqual(request("/v1/assets?tenant_id=northstar", token="a" * 43)[0], 401)
        self.assertEqual(request("/v1/demo/session", payload={"principal_id": "unknown"})[0], 404)
        self.assertEqual(request("/v1/assets?tenant_id=northstar&limit=0", token=self.tokens["demo-northstar"])[0], 422)

    def test_partner_site_and_tenant_scope(self):
        token = self.tokens["demo-partner"]
        status, page = request("/v1/assets?tenant_id=northstar", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 3)
        self.assertEqual({row["site_id"] for row in page["items"]}, {"harbor"})
        self.assertEqual(request("/v1/assets/river-01?tenant_id=northstar", token=token)[0], 404)
        self.assertEqual(request("/v1/assets?tenant_id=summit", token=token)[0], 403)
        self.assertEqual(request("/v1/assets/ridge-01?tenant_id=summit", token=token)[0], 403)

    def test_both_customer_scopes(self):
        token = self.tokens["demo-summit"]
        status, page = request("/v1/assets?tenant_id=summit", token=token)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in page["items"]], ["ridge-01"])
        self.assertEqual(request("/v1/assets?tenant_id=northstar", token=token)[0], 403)

    def test_findings_and_advisory_evidence(self):
        token = self.tokens["demo-partner"]
        for finding, asset_id in (
            ("affected", "harbor-01"),
            ("unaffected", "harbor-02"),
            ("needs_evidence", "harbor-03"),
        ):
            with self.subTest(finding=finding):
                status, page = request(f"/v1/assets?tenant_id=northstar&finding={finding}", token=token)
                self.assertEqual(status, 200)
                self.assertEqual(page["total"], 1)
                self.assertEqual([row["id"] for row in page["items"]], [asset_id])
                status, detail = request(f"/v1/assets/{asset_id}?tenant_id=northstar", token=token)
                self.assertEqual(status, 200)
                self.assertEqual(detail["finding"], finding)
                self.assertEqual(detail["advisory_id"], "CVE-2025-20311")
                self.assertTrue(detail["reason"])
                self.assertNotIn("raw_evidence", detail)

    def test_stable_pagination_and_count(self):
        token = self.tokens["demo-northstar"]
        _, first = request("/v1/assets?tenant_id=northstar&limit=2", token=token)
        _, second = request(f"/v1/assets?tenant_id=northstar&limit=2&after={first['next_cursor']}", token=token)
        self.assertEqual(first["total"], second["total"])
        self.assertEqual(first["total"], 4)
        self.assertIsNone(second["next_cursor"])
        self.assertEqual([row["id"] for row in first["items"] + second["items"]], ["harbor-01", "harbor-02", "harbor-03", "river-01"])


if __name__ == "__main__":
    unittest.main()
