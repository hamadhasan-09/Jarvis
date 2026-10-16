"""Dependency-free regression tests for the Nexus Stage 1 pairing registry."""
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_v2.browser_auth import PairingRegistry


class FakeClock:
    def __init__(self):
        self.t = 10.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.registry = PairingRegistry(
            pairing_ttl_seconds=30,
            credential_ttl_seconds=100,
            clock=self.clock,
        )

    def paired(self):
        token = self.registry.issue_owner_pairing_token()
        result = self.registry.exchange_pairing_token(token, is_loopback=True)
        self.assertIsNotNone(result)
        return result

    def test_never_grants_credentials_without_pairing(self):
        self.assertIsNone(self.registry.validate(None))
        self.assertIsNone(self.registry.validate("made-up"))
        self.assertIsNone(self.registry.exchange_pairing_token("made-up", is_loopback=True))

    def test_pairing_token_stored_only_as_hash(self):
        token = self.registry.issue_owner_pairing_token()
        self.assertNotIn(token, repr(self.registry._pairings))
        self.assertEqual(len(self.registry._pairings), 1)

    def test_pairing_credential_and_one_time_replay(self):
        token = self.registry.issue_owner_pairing_token()
        credential = self.registry.exchange_pairing_token(token, is_loopback=True)
        self.assertEqual(credential.principal_id, "primary-user")
        self.assertIsNotNone(self.registry.validate(credential.credential_id))
        self.assertIsNone(self.registry.exchange_pairing_token(token, is_loopback=True))

    def test_remote_pairing_always_rejected(self):
        token = self.registry.issue_owner_pairing_token()
        self.assertIsNone(self.registry.exchange_pairing_token(token, is_loopback=False))
        self.assertIsNotNone(self.registry.exchange_pairing_token(token, is_loopback=True))

    def test_pairing_token_expiry(self):
        token = self.registry.issue_owner_pairing_token()
        self.clock.advance(30)
        self.assertIsNone(self.registry.exchange_pairing_token(token, is_loopback=True))

    def test_issuing_new_pairing_token_invalidates_old(self):
        old = self.registry.issue_owner_pairing_token()
        new = self.registry.issue_owner_pairing_token()
        self.assertIsNone(self.registry.exchange_pairing_token(old, is_loopback=True))
        self.assertIsNotNone(self.registry.exchange_pairing_token(new, is_loopback=True))

    def test_expiry_on_existing_credential(self):
        credential = self.paired()
        self.clock.advance(100)
        self.assertIsNone(self.registry.validate(credential.credential_id))

    def test_revoke_is_immediate_and_idempotent(self):
        credential = self.paired()
        self.assertTrue(self.registry.revoke(credential.credential_id))
        self.assertFalse(self.registry.revoke(credential.credential_id))
        self.assertIsNone(self.registry.validate(credential.credential_id))

    def test_invalidation_callbacks_on_expiry_and_revocation(self):
        calls = []
        self.registry.register_invalidation_callback(lambda cid, reason: calls.append((cid, reason)))
        a = self.paired()
        self.assertTrue(self.registry.revoke(a.credential_id))
        self.assertEqual(calls, [(a.credential_id, "revoked")])
        b = self.paired()
        self.clock.advance(100)
        self.assertEqual(self.registry.expire_and_notify(), 1)
        self.assertEqual(calls[-1], (b.credential_id, "expired"))
        self.assertEqual(self.registry.expire_and_notify(), 0)

    def test_revoke_all_invalidates_and_rotates_epoch(self):
        a = self.paired()
        b = self.paired()
        self.assertEqual(self.registry.revoke_all(), 2)
        self.assertIsNone(self.registry.validate(a.credential_id))
        self.assertIsNone(self.registry.validate(b.credential_id))
        self.assertIsNotNone(self.paired())

    def test_restart_invalidates_old_credential(self):
        a = self.paired()
        replacement = PairingRegistry(clock=self.clock)
        self.assertIsNone(replacement.validate(a.credential_id))

    def test_concurrent_pairing_has_single_winner(self):
        token = self.registry.issue_owner_pairing_token()
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.registry.exchange_pairing_token(token, is_loopback=True), range(32)))
        self.assertEqual(sum(item is not None for item in results), 1)

    def test_invalid_ttls_fail_closed(self):
        with self.assertRaises(ValueError):
            PairingRegistry(pairing_ttl_seconds=0)
        with self.assertRaises(ValueError):
            PairingRegistry(credential_ttl_seconds=-1)


if __name__ == "__main__":
    unittest.main()
