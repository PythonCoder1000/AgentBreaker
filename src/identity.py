"""Agent identity: capability tokens, scope enforcement, and revocation.

Every tool call evaluated by the interceptor is checked against the
CapabilityToken in effect. Its scope determines what the agent may do; the
revocation registry is checked first, before any other policy tier runs.

Token hierarchy:
  - Root tokens are issued at session start via generate_token().
  - Derived tokens are issued when an agent spawns a sub-agent via derive_token().
    A derived token's scope is always the intersection of the parent's scope and
    the requested scope — a sub-agent can never exceed its parent's permissions.
  - Revoking a root token by ID blocks all derived tokens too (parent ID chain
    check).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

# Per-process signing key. Tokens are only valid within one process lifetime —
# a restarted server issues fresh tokens so stale tokens can't replay.
_SESSION_KEY: bytes = os.urandom(32)

# Global revocation set. Checked at tier 0 of the interceptor before any other
# policy runs, so a revoked agent is blocked immediately, mid-session.
_REVOKED: set[str] = set()

# Parent-map: token_id -> parent_token_id (None for root tokens). Populated by
# generate_token so is_token_revoked can walk the full ancestor chain. Revoking
# a root token blocks all descendants, not just immediate children.
_PARENT_MAP: dict[str, Optional[str]] = {}


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #
@dataclass
class Scope:
    """Capability constraints for one agent session."""

    tools: list[str]  # allowed tool names; subset of all available tools
    bash_allowed: bool = True  # whether run_bash is permitted at all
    email_to: Optional[list[str]] = None  # None = global domain rules; list = restrict to these
    allowed_paths: Optional[list[str]] = None  # None = global rules; list = allowed path prefixes
    services: Optional[list[str]] = None  # None = all broker services; list = restrict call_api to these
    max_depth: int = 2  # max sub-agent nesting levels this token may spawn (0 = no spawning)
    spending_limit_usd: Optional[float] = None  # None = no spending cap; float = max cumulative USD this token may charge

    def intersect(self, other: "Scope") -> "Scope":
        """Return the most restrictive combination of self and other.

        Used by derive_token to ensure a child token can never exceed the
        parent's permissions, regardless of what scope the child requests.
        """
        tools = [t for t in other.tools if t in self.tools]
        bash_allowed = self.bash_allowed and other.bash_allowed

        if self.email_to is not None:
            # Parent restricts; child is a subset of the parent's allowed list.
            parent_set = set(self.email_to)
            child_candidates = other.email_to if other.email_to is not None else self.email_to
            email_to = [e for e in child_candidates if e in parent_set]
        else:
            email_to = other.email_to  # parent unrestricted; child's restriction stands

        if self.allowed_paths is not None and other.allowed_paths is not None:
            # Both restrict; keep only child paths that are a sub-path of at least
            # one parent-allowed prefix. The reverse (ap.startswith(p)) is excluded:
            # a shorter child prefix would cover paths the parent blocks.
            allowed_paths = [
                p for p in other.allowed_paths
                if any(p.startswith(ap) for ap in self.allowed_paths)
            ]
        elif self.allowed_paths is not None:
            allowed_paths = self.allowed_paths
        else:
            allowed_paths = other.allowed_paths

        # Broker services: same rule as email_to — a restricting parent confines
        # the child to a subset; an unrestricted parent lets the child's own
        # restriction stand.
        if self.services is not None:
            parent_services = set(self.services)
            child_service_candidates = other.services if other.services is not None else self.services
            services = [s for s in child_service_candidates if s in parent_services]
        else:
            services = other.services

        # Child loses one depth level from whatever the parent had; can't exceed
        # what the child requested either.
        child_max_depth = max(0, min(self.max_depth - 1, other.max_depth))

        # Spending limit: take the lower (more restrictive) of both limits.
        if self.spending_limit_usd is not None and other.spending_limit_usd is not None:
            spending_limit_usd = min(self.spending_limit_usd, other.spending_limit_usd)
        elif self.spending_limit_usd is not None:
            spending_limit_usd = self.spending_limit_usd
        else:
            spending_limit_usd = other.spending_limit_usd

        return Scope(
            tools=tools,
            bash_allowed=bash_allowed,
            email_to=email_to,
            allowed_paths=allowed_paths,
            services=services,
            max_depth=child_max_depth,
            spending_limit_usd=spending_limit_usd,
        )


# --------------------------------------------------------------------------- #
# CapabilityToken
# --------------------------------------------------------------------------- #
@dataclass
class CapabilityToken:
    """Signed authorization claim for one agent session."""

    token_id: str
    parent_token_id: Optional[str]
    agent_name: str
    principal: str
    issued_at: float
    expires_at: float
    depth: int  # nesting depth: 0 = root, 1 = first sub-agent, …
    scope: Scope
    signature: str = field(default="", repr=False)

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def _payload(self) -> str:
        """Deterministic JSON payload (excludes signature) used for signing."""
        data = {
            "token_id": self.token_id,
            "parent_token_id": self.parent_token_id,
            "agent_name": self.agent_name,
            "principal": self.principal,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "depth": self.depth,
            "scope": asdict(self.scope),
        }
        return json.dumps(data, sort_keys=True)

    def sign(self) -> "CapabilityToken":
        payload = self._payload().encode()
        self.signature = hmac.new(_SESSION_KEY, payload, hashlib.sha256).hexdigest()
        return self

    def verify(self) -> bool:
        """Return True if the signature matches this process's session key."""
        payload = self._payload().encode()
        expected = hmac.new(_SESSION_KEY, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(self.signature, expected)

    def to_display(self) -> dict:
        """Public summary for UI and audit logging (no raw signature)."""
        return {
            "token_id": self.token_id[:8],
            "token_id_full": self.token_id,
            "parent_token_id": self.parent_token_id[:8] if self.parent_token_id else None,
            "agent_name": self.agent_name,
            "principal": self.principal,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "depth": self.depth,
            "scope": {
                "tools": self.scope.tools,
                "bash_allowed": self.scope.bash_allowed,
                "email_to": self.scope.email_to,
                "allowed_paths": self.scope.allowed_paths,
                "services": self.scope.services,
                "max_depth": self.scope.max_depth,
                "spending_limit_usd": self.scope.spending_limit_usd,
            },
            "revoked": is_token_revoked(self),
        }


# --------------------------------------------------------------------------- #
# Token lifecycle
# --------------------------------------------------------------------------- #
def generate_token(
    agent_name: str,
    principal: str,
    scope: Scope,
    parent_token: Optional[CapabilityToken] = None,
    ttl_seconds: int = 3600,
) -> CapabilityToken:
    """Issue a new signed capability token."""
    now = time.time()
    parent_id = parent_token.token_id if parent_token else None
    token = CapabilityToken(
        token_id=str(uuid.uuid4()),
        parent_token_id=parent_id,
        agent_name=agent_name,
        principal=principal,
        issued_at=now,
        expires_at=now + ttl_seconds,
        depth=0 if parent_token is None else parent_token.depth + 1,
        scope=scope,
    )
    token.sign()
    # Register in the parent map so is_token_revoked can walk the full chain.
    _PARENT_MAP[token.token_id] = parent_id
    return token


def derive_token(
    parent: CapabilityToken,
    child_agent_name: str,
    requested_scope: Scope,
    ttl_seconds: int = 3600,
) -> CapabilityToken:
    """Derive a child token whose scope is the intersection of parent's and requested.

    The intersection is computed structurally — it is not possible to request a
    child scope that exceeds the parent's, regardless of what is passed in.
    """
    child_scope = parent.scope.intersect(requested_scope)
    return generate_token(
        agent_name=child_agent_name,
        principal=parent.principal,
        scope=child_scope,
        parent_token=parent,
        ttl_seconds=ttl_seconds,
    )


# --------------------------------------------------------------------------- #
# Revocation
# --------------------------------------------------------------------------- #
def revoke_token(token_id: str) -> None:
    """Add a token ID to the revocation set. All derived tokens are also blocked."""
    _REVOKED.add(token_id)


def is_token_revoked(token: CapabilityToken) -> bool:
    """True if this token or any ancestor in its chain has been revoked.

    Walks the full ancestor chain via _PARENT_MAP so revoking a root token
    blocks all descendants regardless of nesting depth.
    """
    current: Optional[str] = token.token_id
    while current is not None:
        if current in _REVOKED:
            return True
        current = _PARENT_MAP.get(current)
    return False


# --------------------------------------------------------------------------- #
# Tool list filtering
# --------------------------------------------------------------------------- #
def tools_for_scope(all_tools: list, scope: Scope) -> list:
    """Return only the tools the scope permits.

    If max_depth == 0, spawn_subagent is excluded even if it's listed in
    scope.tools, since this token cannot legally issue derived tokens.
    """
    allowed = set(scope.tools)
    if scope.max_depth <= 0:
        allowed.discard("spawn_subagent")
    return [t for t in all_tools if t.get("name") in allowed]
