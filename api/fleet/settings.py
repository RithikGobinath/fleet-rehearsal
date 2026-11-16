from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class LocalSettings:
    database_url: str = field(repr=False)
    fixture_path: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        if env.get("FLEET_DEMO_MODE") != "local":
            raise RuntimeError("Set FLEET_DEMO_MODE=local for the synthetic demo.")
        return cls(*database_config(env))


def database_config(env):
    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Set DATABASE_URL for the synthetic demo.")
    fixture = env.get("FLEET_FIXTURE", "fixtures/inventory.json").strip()
    if not fixture:
        raise RuntimeError("FLEET_FIXTURE must name a synthetic inventory fixture.")
    return database_url, Path(fixture)


@dataclass(frozen=True, slots=True)
class HostedSettings:
    database_url: str = field(repr=False)
    fixture_path: Path
    issuer: str
    audience: str
    client_id: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        issuer = env.get("FLEET_OIDC_ISSUER", "").strip()
        audience = env.get("FLEET_OIDC_AUDIENCE", "").strip()
        client_id = env.get("FLEET_OIDC_CLIENT_ID", "").strip()
        url = urlsplit(issuer)
        if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
                or url.path not in ("", "/") or url.query or url.fragment or not audience or not client_id):
            raise RuntimeError("Set the HTTPS Auth0 issuer, API audience, and public SPA client ID.")
        return cls(*database_config(env), issuer.rstrip("/") + "/", audience, client_id)


def runtime_settings(env: Mapping[str, str]) -> LocalSettings | HostedSettings:
    if env.get("FLEET_DEMO_MODE") == "hosted":
        return HostedSettings.from_env(env)
    return LocalSettings.from_env(env)
