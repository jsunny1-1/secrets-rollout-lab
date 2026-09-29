#!/usr/bin/env python3
"""Synthetic in-memory rotation lab. This is not a production secret manager."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

SECRET_PATH = "demo/orders/service-credential"
ROTATOR = "rotation-controller"


class LabError(Exception):
    """Errors intentionally contain no secret values."""


class AccessDenied(LabError):
    pass


class MissingVersion(LabError):
    pass


class VerificationFailed(LabError):
    pass


class UnsafeRevocation(LabError):
    pass


@dataclass
class Version:
    # The value is excluded from repr to reduce accidental disclosure in this lab.
    value: str = field(repr=False)
    active: bool = True


class SecretStore:
    """Exact-path ACLs and versions, held only in this process's memory."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], Version] = {}
        self._grants: set[tuple[str, str, str]] = set()

    def grant(self, identity: str, action: str, path: str = SECRET_PATH) -> None:
        # Setup-only operation: callers are trusted Python code in this simulation.
        self._grants.add((identity, action, path))

    def check(self, identity: str, action: str, path: str) -> None:
        if (identity, action, path) not in self._grants:
            raise AccessDenied("identity lacks requested action on this path")

    def create(self, identity: str, path: str, version: str) -> None:
        self.check(identity, "write", path)
        if (path, version) in self._versions:
            raise LabError("version already exists")
        # Deliberately predictable, unusable demo data. No cryptographic claim.
        self._versions[(path, version)] = Version(f"SYNTHETIC-ONLY-{path}-{version}")

    def read(self, identity: str, path: str, version: str) -> str:
        self.check(identity, "read", path)
        item = self._versions.get((path, version))
        if item is None or not item.active:
            raise MissingVersion("requested version is absent or inactive")
        return item.value

    def accepts(self, path: str, candidate: str) -> bool:
        """Mock upstream authority accepts both versions during overlap."""
        return any(key[0] == path and item.active and item.value == candidate
                   for key, item in self._versions.items())

    def revoke(self, identity: str, path: str, version: str) -> None:
        self.check(identity, "revoke", path)
        if (path, version) not in self._versions:
            raise MissingVersion("requested version is absent")
        self._versions[(path, version)].active = False

    def active_versions(self, path: str = SECRET_PATH) -> list[str]:
        return sorted(key[1] for key, item in self._versions.items()
                      if key[0] == path and item.active)


@dataclass
class Service:
    name: str
    fail_candidate: bool = False
    fail_rollback: bool = False
    configured_version: str = "v1"
    observed_version: str = "v1"
    healthy: bool = True
    _credential: str = field(default="", repr=False)
    _attempted_candidate: bool = field(default=False, repr=False)

    def activate(self, store: SecretStore, version: str) -> None:
        # Read before changing config: denied/missing versions preserve old state.
        credential = store.read(self.name, SECRET_PATH, version)
        if version == "v2":
            self._attempted_candidate = True
        self.configured_version = version
        self._credential = credential

    def verify(self, store: SecretStore) -> None:
        # A real probe would perform an authenticated operation against an API.
        failure = (
            (self.configured_version == "v2" and self.fail_candidate)
            or (self.configured_version == "v1" and self._attempted_candidate
                and self.fail_rollback)
            or not store.accepts(SECRET_PATH, self._credential)
        )
        self.healthy = not failure
        if failure:
            raise VerificationFailed("synthetic authenticated probe failed")
        self.observed_version = self.configured_version

    def snapshot(self) -> dict:
        return {"service": self.name, "configured_version": self.configured_version,
                "observed_version": self.observed_version, "healthy": self.healthy}


class Rotation:
    def __init__(self, store: SecretStore, services: list[Service]) -> None:
        self.store = store
        self.services = services
        self.events: list[dict] = []

    def event(self, action: str, **details: object) -> None:
        self.events.append({"sequence": len(self.events) + 1, "action": action,
                            **details})

    def verify_service(self, service: Service, version: str, phase: str) -> None:
        service.activate(self.store, version)
        service.verify(self.store)
        self.event("consumer_verified", service=service.name,
                   version=version, phase=phase)

    def retire(self, old: str, replacement: str) -> None:
        # A synchronous toy gate, not a distributed consistency guarantee.
        if not self.services or any(
            not service.healthy or service.configured_version != replacement
            or service.observed_version != replacement for service in self.services
        ):
            raise UnsafeRevocation("not every registered consumer is verified")
        self.store.revoke(ROTATOR, SECRET_PATH, old)
        self.event("version_revoked", version=old,
                   verified_consumers=len(self.services))

    def rollback(self) -> bool:
        success = True
        # Recheck the full declared inventory, including untouched consumers.
        for service in self.services:
            try:
                self.verify_service(service, "v1", "rollback")
            except LabError as exc:
                success = False
                self.event("rollback_failed", service=service.name,
                           error_type=type(exc).__name__)
        if success:
            self.retire("v2", "v1")
        else:
            # Retain both versions because consumer state is not fully verified.
            self.event("manual_recovery_required", retained_versions=["v1", "v2"])
        return success

    def run(self) -> dict:
        if not self.services:
            raise LabError("at least one registered consumer is required")
        self.store.create(ROTATOR, SECRET_PATH, "v2")
        self.event("candidate_created", version="v2", prior_version="v1")
        try:
            for index, service in enumerate(self.services):
                phase = "canary" if index == 0 else "remaining_rollout"
                self.verify_service(service, "v2", phase)
        except LabError as exc:
            self.event("promotion_stopped", service=service.name,
                       phase=phase, error_type=type(exc).__name__)
            rolled_back = self.rollback()
            return self.report("SAFE_ROLLBACK" if rolled_back else "RECOVERY_REQUIRED")
        self.retire("v1", "v2")
        return self.report("ROTATED")

    def report(self, status: str) -> dict:
        return {
            "schema_version": 1,
            "mode": "synthetic_in_memory_simulation",
            "status": status,
            "registered_consumers": len(self.services),
            "active_versions": self.store.active_versions(),
            "services": [service.snapshot() for service in self.services],
            "events": self.events,
            "claims": {"real_provider_integration": False,
                       "production_security_validated": False,
                       "distributed_safety_validated": False},
        }


SCENARIOS = ("happy", "canary-failure", "consumer-failure", "rollback-failure")


def build(scenario: str = "happy") -> Rotation:
    if scenario not in SCENARIOS:
        raise ValueError("unknown scenario")
    store = SecretStore()
    store.grant(ROTATOR, "write")
    store.grant(ROTATOR, "revoke")
    store.create(ROTATOR, SECRET_PATH, "v1")
    services = [Service(f"worker-{letter}") for letter in "abc"]
    for service in services:
        store.grant(service.name, "read")
        service.activate(store, "v1")
        service.verify(store)
    if scenario in ("canary-failure", "rollback-failure"):
        services[0].fail_candidate = True
    if scenario == "consumer-failure":
        services[1].fail_candidate = True
    if scenario == "rollback-failure":
        services[0].fail_rollback = True
    return Rotation(store, services)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="happy")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    result = build(args.scenario).run()
    result["scenario"] = args.scenario
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return {"ROTATED": 0, "SAFE_ROLLBACK": 2, "RECOVERY_REQUIRED": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
