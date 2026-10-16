"""Server-side, single-user browser pairing and credential registry for Nexus.

Integration requirement: issue_owner_pairing_token() must be called ONLY by a
trusted owner-controlled bootstrap. Never expose it on a public HTTP endpoint.
This module alone does not authenticate the Windows owner or secure Socket.IO.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import logging
import math
import secrets
import threading
import time
from typing import Callable
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True, repr=False)
class BrowserCredential:
    """Private server record. Never serialize credential_id into HTTP JSON."""

    credential_id: str
    principal_id: str
    issued_at: float
    expires_at: float
    auth_epoch: str


@dataclass(slots=True)
class _CredentialState:
    record: BrowserCredential
    revoked: bool = False


class PairingRegistry:
    """In-memory registry. All checks and token consumption are thread-safe.

    A Flask signed cookie should carry only `credential_id`; validation must
    consult this registry for EVERY privileged operation, not just Socket.IO
    connect. Call `expire_and_notify` from a scheduled server task to actively
    invalidate idle connected sockets after their credentials expire.

    `on_invalidate` callbacks receive (credential_id, reason). Integration must
    deny pending approvals and revoke active Socket.IO capabilities for that ID.
    Callbacks run *outside* the registry mutex.
    """

    def __init__(
        self,
        *,
        pairing_ttl_seconds: int = 120,
        credential_ttl_seconds: int = 8 * 60 * 60,
        principal_id: str = "primary-user",
        clock: Callable[[], float] = time.monotonic,
        schedule_expiry: bool = False,
    ) -> None:
        if (not math.isfinite(pairing_ttl_seconds) or not math.isfinite(credential_ttl_seconds)
                or pairing_ttl_seconds <= 0 or credential_ttl_seconds <= 0):
            raise ValueError("Credential and pairing TTL must be positive")
        if not principal_id:
            raise ValueError("principal_id must not be empty")
        self._lock = threading.RLock()
        self._clock = clock
        self._pairing_ttl = pairing_ttl_seconds
        self._credential_ttl = credential_ttl_seconds
        self._principal_id = principal_id
        self._auth_epoch = secrets.token_urlsafe(32)
        self._pairings: dict[str, float] = {}
        self._credentials: dict[str, _CredentialState] = {}
        self._listeners: list[Callable[[str, str], None]] = []
        self._schedule_expiry = schedule_expiry
        self._timers: dict[str, threading.Timer] = {}

    def register_invalidation_callback(self, callback: Callable[[str, str], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def issue_owner_pairing_token(self) -> str:
        """TRUSTED CALL SITES ONLY: token must never be offered from GET /."""
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._lock:
            # Only the most recently owner-issued, unconsumed token remains valid.
            self._pairings.clear()
            self._pairings[token_hash] = self._clock() + self._pairing_ttl
        return token

    def exchange_pairing_token(
        self, token: str, *, is_loopback: bool
    ) -> BrowserCredential | None:
        """Consume exactly once; deny non-loopback pairing regardless of token."""
        if not is_loopback or not isinstance(token, str) or not token or len(token) > 1024:
            return None
        try:
            token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        except UnicodeEncodeError:
            return None
        self.expire_and_notify()
        with self._lock:
            # The partial foundation retained invalidated records indefinitely.
            # Bound live grants and prune tombstones before creating another.
            self._credentials = {cid: state for cid, state in self._credentials.items() if not state.revoked}
            if len(self._credentials) >= 128:
                return None
            now = self._clock()
            found_hash = next(
                (h for h in self._pairings if hmac.compare_digest(h, token_hash)),
                None,
            )
            if found_hash is None:
                return None
            expiry = self._pairings.pop(found_hash)
            if now >= expiry:
                return None
            record = BrowserCredential(
                credential_id=secrets.token_urlsafe(32),
                principal_id=self._principal_id,
                issued_at=now,
                expires_at=now + self._credential_ttl,
                auth_epoch=self._auth_epoch,
            )
            self._credentials[record.credential_id] = _CredentialState(record=record)
            if self._schedule_expiry:
                self._schedule_locked(record)
            return record

    def _schedule_locked(self, record):
        delay = max(0.001, record.expires_at - self._clock())
        timer = threading.Timer(delay, self._expire_scheduled, args=(record.credential_id,))
        timer.daemon = True
        self._timers[record.credential_id] = timer
        try:
            timer.start()
        except Exception:
            self._timers.pop(record.credential_id, None)
            self._credentials.pop(record.credential_id, None)
            raise

    def _expire_scheduled(self, credential_id):
        self.expire_and_notify()
        with self._lock:
            state = self._credentials.get(credential_id)
            if state is not None and not state.revoked:
                # A timer firing slightly early must not leave an idle grant
                # alive indefinitely. Reschedule against the actual deadline.
                self._schedule_locked(state.record)

    def _invalidate_locked(self, credential_id: str, reason: str) -> bool:
        state = self._credentials.get(credential_id)
        if state is None or state.revoked:
            return False
        state.revoked = True
        timer = self._timers.pop(credential_id, None)
        if timer:
            timer.cancel()
        return True

    def _notify(self, invalidations: list[tuple[str, str]]) -> None:
        if not invalidations:
            return
        with self._lock:
            listeners = tuple(self._listeners)
        for credential_id, reason in invalidations:
            for callback in listeners:
                try:
                    callback(credential_id, reason)
                except Exception:
                    logging.getLogger(__name__).exception("Browser credential invalidation callback failed")

    def validate(self, credential_id: str | None) -> BrowserCredential | None:
        """Per-operation validation, including expiry, revocation and epoch."""
        if not isinstance(credential_id, str) or not credential_id:
            return None
        invalidations: list[tuple[str, str]] = []
        with self._lock:
            state = self._credentials.get(credential_id)
            if state is None or state.revoked:
                return None
            record = state.record
            if record.auth_epoch != self._auth_epoch:
                if self._invalidate_locked(credential_id, "epoch_changed"):
                    invalidations.append((credential_id, "epoch_changed"))
                result = None
            elif self._clock() >= record.expires_at:
                if self._invalidate_locked(credential_id, "expired"):
                    invalidations.append((credential_id, "expired"))
                result = None
            else:
                result = record
        self._notify(invalidations)
        return result

    def revoke(self, credential_id: str | None) -> bool:
        if not isinstance(credential_id, str) or not credential_id:
            return False
        with self._lock:
            changed = self._invalidate_locked(credential_id, "revoked")
        if changed:
            self._notify([(credential_id, "revoked")])
        return changed

    def expire_and_notify(self) -> int:
        """Actively invalidate expired connected sessions; call periodically."""
        invalidations: list[tuple[str, str]] = []
        with self._lock:
            now = self._clock()
            for credential_id, state in self._credentials.items():
                if not state.revoked and now >= state.record.expires_at:
                    if self._invalidate_locked(credential_id, "expired"):
                        invalidations.append((credential_id, "expired"))
            self._pairings = {h: expiry for h, expiry in self._pairings.items() if now < expiry}
        self._notify(invalidations)
        return len(invalidations)

    def revoke_all(self) -> int:
        """Rotate auth epoch and invalidate every active browser credential."""
        invalidations: list[tuple[str, str]] = []
        with self._lock:
            for credential_id, state in self._credentials.items():
                if not state.revoked:
                    if self._invalidate_locked(credential_id, "epoch_changed"):
                        invalidations.append((credential_id, "epoch_changed"))
            self._auth_epoch = secrets.token_urlsafe(32)
            self._pairings.clear()
        self._notify(invalidations)
        return len(invalidations)


# The HTTP adapter stays local to Nexus; the supplied registry remains the core.
SESSION_KEY = "nexus_owner_credential_v1"
COOKIE_NAME = "nexus_owner_session_v1"
CSRF_HEADER = "X-Nexus-Browser-Auth"


def loopback_host(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def local_browser_request(bind_host, port):
    """Ignore forwarded headers; reject remote binds, DNS rebinding and CSRF origins."""
    from flask import request

    if not loopback_host(bind_host) or not loopback_host(request.remote_addr or ""):
        return False
    try:
        target = urlsplit(request.host_url)
        if not loopback_host(target.hostname or "") or (target.port or 80) != port:
            return False
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return False
    except ValueError:
        return False
    return True


def browser_auth_blueprint(registry, *, bind_host, port):
    from flask import Blueprint, jsonify, request, session

    blueprint = Blueprint("browser_auth", __name__)

    @blueprint.before_request
    def local_only():
        if not local_browser_request(bind_host(), port()):
            return jsonify({"authenticated": False, "error": "forbidden"}), 403
        # A non-simple header prevents form/opaque-origin cookie mutations.
        if request.method != "GET" and request.headers.get(CSRF_HEADER) != "1":
            return jsonify({"authenticated": False, "error": "forbidden"}), 403

    @blueprint.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @blueprint.post("/auth/browser/pair")
    @blueprint.post("/auth/pair")
    def pair():
        payload = request.get_json(silent=True)
        credential = registry.exchange_pairing_token(
            payload.get("token") if isinstance(payload, dict) else None,
            is_loopback=local_browser_request(bind_host(), port()),
        )
        if credential is None:
            return jsonify({"authenticated": False, "error": "invalid_pairing_token"}), 401
        old = session.get(SESSION_KEY)
        session.clear()
        session[SESSION_KEY] = credential.credential_id
        registry.revoke(old)
        return jsonify({"authenticated": True})

    @blueprint.get("/auth/browser/status")
    @blueprint.get("/auth/status")
    def status():
        authenticated = registry.validate(session.get(SESSION_KEY)) is not None
        return jsonify({"authenticated": authenticated}), 200 if authenticated else 401

    @blueprint.post("/auth/browser/revoke")
    @blueprint.post("/auth/revoke")
    def revoke():
        credential = session.get(SESSION_KEY)
        if not registry.validate(credential):
            session.clear()
            return jsonify({"authenticated": False, "error": "unauthorized"}), 401
        registry.revoke(credential)
        session.clear()
        return jsonify({"authenticated": False})

    return blueprint
