import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from fleet.main import app
from test_rehearsal import call, connect


class HostedBoundaryTests(unittest.TestCase):
    def test_hosted_verifies_tokens_and_current_database_grants(self):
        subject = "auth0|" + uuid4().hex
        issuer = "https://example.invalid/"
        audience = "https://fleet.example.invalid"
        client_id = "fixture-spa"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        env = {"FLEET_DEMO_MODE": "hosted", "FLEET_OIDC_ISSUER": issuer,
               "FLEET_OIDC_AUDIENCE": audience, "FLEET_OIDC_CLIENT_ID": client_id}
        with connect() as db:
            db.execute("INSERT INTO hosted_identities VALUES (%s,%s,'demo-partner')", (issuer, subject))
        self.addCleanup(self.remove_identity, issuer, subject)
        local = call("/v1/demo/session", body={"principal_id": "demo-partner"})[1]["access_token"]
        claims = {"iss": issuer, "aud": audience, "sub": subject, "azp": client_id,
                  "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}

        def token(**override):
            return jwt.encode({**claims, **override}, key, algorithm="RS256", headers={"kid": "fixture"})

        def headers(value):
            return {"Authorization": "Bearer " + value}

        with patch.dict(os.environ, env), patch("fleet.hosted_auth.jwt.PyJWKClient.get_signing_key",
                return_value=SimpleNamespace(key=key.public_key())), TestClient(app) as client:
            self.assertEqual(client.get("/v1/auth/config").json()["mode"], "hosted")
            self.assertEqual(client.get("/v1/demo/principals").status_code, 404)
            self.assertEqual(client.post("/v1/demo/session", json={"principal_id": "demo-partner"}).status_code, 404)
            for denied in (local, token(exp=1), token(aud="another-audience"), token(iss="https://elsewhere.invalid/"),
                           token(azp="other-client"), token(sub="unknown")):
                self.assertEqual(client.get("/v1/auth/profile", headers=headers(denied)).status_code, 401)
            authorized = headers(token())
            self.assertEqual(client.get("/v1/auth/profile", headers=authorized).json()["id"], "demo-partner")
            visible = client.get("/v1/assets?tenant_id=northstar&limit=1", headers=authorized).json()
            self.assertEqual(visible["total"], 3)
            for path in (
                f"/v1/assets?tenant_id=summit&after={visible['next_cursor']}",
                "/v1/assets/ridge-01?tenant_id=summit",
                "/v1/tenants/summit/sites/ridge/plans/dispatch/revisions/1",
                "/v1/tenants/summit/sites/ridge/observations/quarantine",
                "/v1/tenants/summit/sites/ridge/audit?after=invalid",
            ):
                response = client.get(path, headers=authorized)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("items", response.json())
            denied = client.post("/v1/tenants/northstar/sites/harbor/plans/dispatch/revisions/1/approval",
                headers={**authorized, "Idempotency-Key": uuid4().hex}, json={"expected_fingerprint": "a" * 64})
            self.assertEqual(denied.status_code, 403)
            denied_start = client.post("/v1/tenants/northstar/sites/harbor/plans/dispatch/revisions/1/waves/1/runs",
                headers={**authorized, "Idempotency-Key": uuid4().hex}, json={"expected_fingerprint": "a" * 64})
            self.assertEqual(denied_start.status_code, 403)
            with connect() as db:
                db.execute("DELETE FROM hosted_identities WHERE (issuer,subject)=(%s,%s)", (issuer, subject))
            self.assertEqual(client.get("/v1/auth/profile", headers=authorized).status_code, 401)

    @staticmethod
    def remove_identity(issuer, subject):
        with connect() as db:
            db.execute("DELETE FROM hosted_identities WHERE (issuer,subject)=(%s,%s)", (issuer, subject))
