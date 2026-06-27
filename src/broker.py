"""Runtime secret broker — the access layer the Breaker Agent acts through.

The agent calls call_api(service, action, payload). It never sees, stores, or
passes a credential. This module leases the referenced secret at runtime, uses it
to authenticate the (simulated) external call, and returns only the result — the
secret value never enters the model's context.

Backends are pluggable (VaultBackend). The default LocalVaultBackend resolves a
secret from an environment variable when one is set — so a real secret can be
injected at runtime by 1Password (a service account, `op run`, or the
Environments beta) — and otherwise mints a synthetic, per-process secret so the
demo runs with zero external setup. To wire real 1Password, populate the env vars
in settings.BROKER_SECRET_ENV from your vault (or call set_backend with a custom
VaultBackend); no other code changes are needed.

A leased secret is held only inside call(): it authenticates the simulated
service, contributes a one-way fingerprint to the result (proof a real credential
was used, NOT the credential), and is then discarded. The brokered access is
written to the tamper-evident audit log by secret_ref and fingerprint — never by
value — so "who reached what, with which credential" is reproducible after the
fact without ever recording the secret.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets as _secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import audit
from identity import CapabilityToken
from settings import (
    BROKER_LEASE_TTL_SECONDS,
    BROKER_NO_CREDENTIAL_TEMPLATE,
    BROKER_OUT_OF_SCOPE_TEMPLATE,
    BROKER_RESULT_TEMPLATE,
    BROKER_SECRET_ENV,
    BROKER_SERVICES,
    BROKER_SYNTHETIC_PREFIX,
    BROKER_UNKNOWN_SERVICE_TEMPLATE,
)


# --------------------------------------------------------------------------- #
# Vault backend (pluggable)
# --------------------------------------------------------------------------- #
class VaultBackend:
    """Resolves a named secret reference to its value (or None if unknown)."""

    def resolve(self, secret_ref: str) -> Optional[str]:  # pragma: no cover - interface
        raise NotImplementedError


class LocalVaultBackend(VaultBackend):
    """Default backend: env-injected secret if present, else a per-process synthetic.

    A secret injected via the secret_ref's environment variable (e.g. populated by
    a 1Password service account / `op run`) takes precedence. Otherwise a synthetic
    value is minted once per process and reused — high-entropy, realistic-looking,
    and authenticating to nothing — so the demo never needs real credentials.
    """

    def __init__(self) -> None:
        self._synthetic: dict[str, str] = {}

    def resolve(self, secret_ref: str) -> Optional[str]:
        if secret_ref not in BROKER_SECRET_ENV:
            return None  # not a known reference — the broker has no such secret
        env_name = BROKER_SECRET_ENV[secret_ref]
        injected = os.getenv(env_name)
        if injected:
            return injected
        if secret_ref not in self._synthetic:
            prefix = BROKER_SYNTHETIC_PREFIX.get(secret_ref, "sk_")
            self._synthetic[secret_ref] = prefix + _secrets.token_hex(20)
        return self._synthetic[secret_ref]


# The active backend. Swap it (e.g. for a real 1Password backend) via set_backend.
_BACKEND: VaultBackend = LocalVaultBackend()

# Per-process key for the fingerprint HMAC. Keying it (rather than a plain hash of
# the value) means the fingerprint is not a confirmation oracle: an observer of a
# fingerprint cannot offline-test a guessed secret without this in-process key.
_FP_KEY: bytes = os.urandom(32)


def set_backend(backend: VaultBackend) -> None:
    """Replace the active vault backend (the seam for real 1Password wiring)."""
    global _BACKEND
    _BACKEND = backend


# --------------------------------------------------------------------------- #
# Secret leases
# --------------------------------------------------------------------------- #
@dataclass
class SecretLease:
    """A short-lived runtime lease of one secret. Held only inside the broker."""

    lease_id: str
    secret_ref: str
    value: str
    issued_at: float
    expires_at: float

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def fingerprint(self) -> str:
        """A one-way tag of the secret — proves a real credential was used without
        revealing it. A short keyed-HMAC prefix is neither reversible nor a
        confirmation oracle, so it is safe to place in the model's context and the
        audit log."""
        digest = hmac.new(_FP_KEY, self.value.encode("utf-8"), hashlib.sha256).hexdigest()[:8]
        return f"vault:{self.secret_ref}:{digest}"


def lease_secret(secret_ref: str, ttl_seconds: int = BROKER_LEASE_TTL_SECONDS) -> Optional[SecretLease]:
    """Lease the referenced secret from the active backend, or None if unknown."""
    value = _BACKEND.resolve(secret_ref)
    if value is None:
        return None
    now = time.time()
    return SecretLease(
        lease_id=str(uuid.uuid4()),
        secret_ref=secret_ref,
        value=value,
        issued_at=now,
        expires_at=now + ttl_seconds,
    )


def live_secret_values() -> set[str]:
    """Every secret value the broker currently holds — for the context inspector to
    confirm none of them ever appears in the model's context. Resolving mints any
    not-yet-minted synthetic secret (idempotent), so the set is complete."""
    values: set[str] = set()
    for ref in BROKER_SECRET_ENV:
        value = _BACKEND.resolve(ref)
        if value:
            values.add(value)
    return values


def available_services() -> list[str]:
    return list(BROKER_SERVICES.keys())


# --------------------------------------------------------------------------- #
# Brokered call
# --------------------------------------------------------------------------- #
def call(
    service: str,
    action: str,
    payload: Optional[dict],
    *,
    token: Optional[CapabilityToken] = None,
    session_id: str = "",
    agent_name: str = "breaker",
    task: str = "",
) -> str:
    """Authenticate and run one brokered service call; return only the result.

    The credential is leased, used to authenticate the simulated call, and
    discarded. It is never placed in the returned string. The access is recorded
    in the audit log by secret_ref and fingerprint, never by value.
    """
    spec = BROKER_SERVICES.get(service)
    if spec is None:
        return BROKER_UNKNOWN_SERVICE_TEMPLATE.format(
            service=service, services=", ".join(BROKER_SERVICES.keys())
        )

    # Service-scope enforcement (defense in depth — tier 0 of the interceptor also
    # checks this before dispatch, but the broker must never trust its caller).
    scope_services = getattr(token.scope, "services", None) if token is not None else None
    if scope_services is not None and service not in scope_services:
        return BROKER_OUT_OF_SCOPE_TEMPLATE.format(service=service)

    lease = lease_secret(spec["secret_ref"])
    if lease is None:
        return BROKER_NO_CREDENTIAL_TEMPLATE.format(secret_ref=spec["secret_ref"])

    fingerprint = lease.fingerprint()
    action_str = str(action).strip() or "request"

    # Record the brokered access (secret_ref + fingerprint, never the value).
    if session_id:
        audit.log_event(
            session_id=session_id,
            token_id=token.token_id[:8] if token is not None else None,
            agent_name=agent_name,
            tool_name="call_api",
            input_summary=f"service={service} action={action_str}",
            tier="broker",
            decision="brokered",
            reason=(
                f"leased {spec['secret_ref']} ({fingerprint}); credential injected "
                "at runtime and never entered the model context"
            ),
            task=task,
            files_read=[],
        )

    detail = spec["response"].format(action=action_str)
    # The lease falls out of scope here — the value is neither stored nor returned.
    return BROKER_RESULT_TEMPLATE.format(
        label=spec["label"],
        secret_ref=spec["secret_ref"],
        fingerprint=fingerprint,
        detail=detail,
    )
