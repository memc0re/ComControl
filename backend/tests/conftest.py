"""Shared fixtures: isolated temporary database + simulator, no real hardware."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from comcontrol.hardware.simulator import SimulatedCommander, make_preset
from comcontrol.main import build
from comcontrol.runtime import Runtime
from comcontrol.settings import Settings


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_settings(data_dir: Path, **overrides) -> Settings:
    s = Settings(data_dir=data_dir, simulate="minimal")
    # cheap hashing keeps the suite fast; production uses the defaults
    s.argon2_time_cost, s.argon2_memory_kib, s.argon2_parallelism = 1, 1024, 1
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


test_settings.__test__ = False  # not a test


@dataclass
class Env:
    client: TestClient
    rt: Runtime
    sim: SimulatedCommander
    clock: FakeClock
    csrf: str | None = None
    extra: dict = field(default_factory=dict)

    def tick(self) -> None:
        """One poll cycle, 5 s after the previous one (like the real loop)."""
        self.clock.advance(5)
        self.client.portal.call(self.rt.tick)  # type: ignore[union-attr]

    def login(self, password: str = "admin") -> dict:
        r = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": password})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        return r.json()

    @property
    def h(self) -> dict:
        return {"X-CSRF-Token": self.csrf or ""}

    def revision(self) -> int:
        return self.client.get("/api/v1/config").json()["revision"]

    def put_fan(self, fan: str, **body) -> object:
        body.setdefault("revision", self.revision())
        return self.client.put(f"/api/v1/fans/{fan}", json=body, headers=self.h)

    def put_sensor(self, sensor: str, **body) -> object:
        body.setdefault("revision", self.revision())
        return self.client.put(f"/api/v1/sensors/{sensor}", json=body, headers=self.h)

    def apply(self, fan: str, **body) -> object:
        body.setdefault("revision", self.revision())
        return self.client.post(f"/api/v1/fans/{fan}/apply", json=body, headers=self.h)

    def state(self) -> dict:
        r = self.client.get("/api/v1/state")
        assert r.status_code == 200, r.text
        return r.json()


def make_env(tmp_path: Path, preset: str = "minimal", **overrides) -> Iterator[Env]:
    settings = test_settings(tmp_path / "data", simulate=preset, **overrides)
    sim = make_preset(preset)
    app, rt, db = build(settings, simulator=sim)
    clock = FakeClock()
    rt.mono = clock
    # no background polling: tests drive poll cycles explicitly via env.tick()
    rt._loop = _no_loop  # type: ignore[method-assign]
    with TestClient(app, base_url="http://testserver") as client:
        env = Env(client, rt, sim, clock)
        yield env
    db.close()


async def _no_loop() -> None:
    return None


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    yield from make_env(tmp_path)


@pytest.fixture
def env_factory(tmp_path: Path):
    """Create environments for other presets; closed at test end."""
    gens = []

    def factory(preset: str = "minimal", sub: str = "a", **overrides) -> Env:
        gen = make_env(tmp_path / sub, preset, **overrides)
        gens.append(gen)
        return next(gen)

    yield factory
    for gen in gens:
        try:
            next(gen)
        except StopIteration:
            pass
