"""Acceptance checks for behavior that matters to a safe rotation decision."""
import json
import unittest

from lab import (AccessDenied, MissingVersion, ROTATOR, SECRET_PATH,
                 UnsafeRevocation, build)


class RotationTests(unittest.TestCase):
    def test_success_revokes_old_only_after_every_consumer_verified(self):
        rotation = build()
        old_credential = rotation.store.read("worker-a", SECRET_PATH, "v1")
        real_revoke = rotation.store.revoke
        states_at_revocation = []

        def record_revoke(*args):
            states_at_revocation.append([service.snapshot() for service in rotation.services])
            real_revoke(*args)

        rotation.store.revoke = record_revoke
        report = rotation.run()
        self.assertEqual(report["status"], "ROTATED")
        self.assertEqual(report["active_versions"], ["v2"])
        self.assertFalse(rotation.store.accepts(SECRET_PATH, old_credential))
        self.assertEqual(len(states_at_revocation), 1)
        self.assertTrue(all(item["healthy"] and item["observed_version"] == "v2"
                            for item in states_at_revocation[0]))
        self.assertEqual(report["events"][1]["phase"], "canary")
        with self.assertRaises(MissingVersion):
            rotation.store.read("worker-a", SECRET_PATH, "v1")

    def test_canary_failure_prevents_remaining_promotion(self):
        rotation = build("canary-failure")
        report = rotation.run()
        self.assertEqual(report["status"], "SAFE_ROLLBACK")
        self.assertEqual(report["active_versions"], ["v1"])
        self.assertFalse(any(event.get("phase") == "remaining_rollout"
                             for event in report["events"]))
        self.assertFalse(any(service._attempted_candidate
                             for service in rotation.services[1:]))
        self.assertTrue(all(item["healthy"] and item["observed_version"] == "v1"
                            for item in report["services"]))

    def test_downstream_failure_rolls_back_already_promoted_canary(self):
        report = build("consumer-failure").run()
        self.assertEqual(report["status"], "SAFE_ROLLBACK")
        self.assertEqual(report["active_versions"], ["v1"])
        self.assertTrue(all(item["configured_version"] == "v1"
                            for item in report["services"]))
        verified_v2 = [event["service"] for event in report["events"]
                       if event["action"] == "consumer_verified"
                       and event["version"] == "v2"]
        self.assertEqual(verified_v2, ["worker-a"])

    def test_unverified_rollback_retains_both_versions(self):
        report = build("rollback-failure").run()
        self.assertEqual(report["status"], "RECOVERY_REQUIRED")
        self.assertEqual(report["active_versions"], ["v1", "v2"])
        self.assertFalse(any(event["action"] == "version_revoked"
                             for event in report["events"]))
        self.assertFalse(report["services"][0]["healthy"])

    def test_reader_cannot_create_or_revoke(self):
        rotation = build()
        for action in (rotation.store.create, rotation.store.revoke):
            with self.assertRaises(AccessDenied):
                action("worker-a", SECRET_PATH, "v1")

    def test_unknown_identity_and_wrong_path_are_denied(self):
        store = build().store
        with self.assertRaises(AccessDenied):
            store.read("unknown-worker", SECRET_PATH, "v1")
        with self.assertRaises(AccessDenied):
            store.read("worker-a", "demo/another-service/credential", "v1")
        with self.assertRaises(AccessDenied):
            store.read(ROTATOR, SECRET_PATH, "v1")

    def test_missing_secret_does_not_mutate_service_configuration(self):
        rotation = build()
        service = rotation.services[0]
        before = service.snapshot()
        with self.assertRaises(MissingVersion):
            service.activate(rotation.store, "missing-version")
        self.assertEqual(service.snapshot(), before)

    def test_version_identifiers_do_not_leak_between_paths(self):
        store = build().store
        other_path = "demo/another-service/credential"
        store.grant("other-worker", "read", other_path)
        with self.assertRaises(MissingVersion):
            store.read("other-worker", other_path, "v1")
        store.grant(ROTATOR, "write", other_path)
        store.create(ROTATOR, other_path, "v1")
        other_value = store.read("other-worker", other_path, "v1")
        self.assertFalse(store.accepts(SECRET_PATH, other_value))

    def test_revocation_gate_rejects_unverified_or_stale_consumers(self):
        rotation = build()
        rotation.store.create(ROTATOR, SECRET_PATH, "v2")
        with self.assertRaises(UnsafeRevocation):
            rotation.retire("v1", "v2")
        for service in rotation.services:
            service.activate(rotation.store, "v2")
        # Updating desired configuration alone is not successful adoption.
        with self.assertRaises(UnsafeRevocation):
            rotation.retire("v1", "v2")
        for service in rotation.services:
            service.verify(rotation.store)
        rotation.services[-1].healthy = False
        with self.assertRaises(UnsafeRevocation):
            rotation.retire("v1", "v2")
        self.assertIn("v1", rotation.store.active_versions())

    def test_empty_inventory_cannot_authorize_revocation(self):
        rotation = build()
        rotation.services.clear()
        with self.assertRaises(UnsafeRevocation):
            rotation.retire("v1", "v2")

    def test_reports_are_deterministic_and_exclude_mock_secret_values(self):
        for scenario in ("happy", "canary-failure", "consumer-failure", "rollback-failure"):
            report = build(scenario).run()
            self.assertEqual(report, build(scenario).run())
            self.assertNotIn("SYNTHETIC-ONLY", json.dumps(report))
        rotation = build()
        self.assertNotIn("SYNTHETIC-ONLY", repr(rotation.services))
        self.assertNotIn("SYNTHETIC-ONLY", repr(rotation.store._versions))


if __name__ == "__main__":
    unittest.main()
