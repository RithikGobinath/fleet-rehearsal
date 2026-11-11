from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self


@dataclass(frozen=True, slots=True)
class LocalSettings:
    database_url: str = field(repr=False)
    fixture_path: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        if env.get("FLEET_DEMO_MODE") != "local":
            raise RuntimeError("Set FLEET_DEMO_MODE=local for the synthetic demo.")
        database_url = env.get("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("Set DATABASE_URL for the synthetic demo.")
        fixture = env.get("FLEET_FIXTURE", "fixtures/inventory.json").strip()
        if not fixture:
            raise RuntimeError("FLEET_FIXTURE must name a synthetic inventory fixture.")
        return cls(database_url=database_url, fixture_path=Path(fixture))
