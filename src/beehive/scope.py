"""Scope model for skill-facing Beehive credentials (decision D3: the agent never touches money).

Token model (what the runtime enforces):
  * the runtime's long-lived credential is the account's AK/SK pair (revocable,
    never the password);
  * every *skill* run gets a short-lived token carrying an explicit scope set
    (default: jobs:write, jobs:read, assets:read -- never billing/admin);
  * the scope gate refuses out-of-scope requests CLIENT-SIDE, before any HTTP
    call, so an agent that goes off the rails cannot even attempt a charge.
  * server-side RBAC is the second layer: a non-admin caller hitting an
    admin-only route gets 403. Note the live gap measured in T1: with a plain
    non-admin token, admin/billing *writes* return 403 but `GET /billing/wallet`
    and `GET /billing/history` return 200 -- the server-side scope middleware
    does not exist yet (pv-beehive-core#585 T3), so this client-side gate is the
    only thing protecting billing today.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field

DEFAULT_SCOPE = ("jobs:write", "jobs:read", "assets:read")

# Scopes that must never be handed to skill-facing code, whatever the caller asks.
FORBIDDEN_SCOPES = ("billing:write", "billing:read", "admin:read", "admin:write")

# (method, path-prefix) -> scope required. Anything not listed is refused.
SCOPE_RULES = [
    ("POST", "/api/v1/jobs", "jobs:write"),
    ("GET", "/api/v1/jobs", "jobs:read"),
    ("DELETE", "/api/v1/jobs", "jobs:write"),
    ("POST", "/api/v1/jobs/", "jobs:write"),
    ("GET", "/api/v1/jobs/", "jobs:read"),
    ("GET", "/api/v1/assets", "assets:read"),
    ("GET", "/api/v1/nodes", "jobs:read"),
    ("POST", "/api/v1/billing/quote", "jobs:write"),
]
DENIED_PREFIXES = ("/api/v1/admin/", "/api/v1/billing/wallet", "/api/v1/billing/nodes",
                   "/api/v1/billing/history", "/api/v1/auth/api-keys")


class ScopeRefusal(PermissionError):
    """The runtime refused to make the call -- before any credential was used."""


@dataclass
class RestrictedToken:
    access_key: str
    secret_key: str
    scope: tuple[str, ...] = DEFAULT_SCOPE
    account: str = ""
    issued_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        bad = sorted(set(self.scope) & set(FORBIDDEN_SCOPES))
        if bad:
            raise ScopeRefusal(f"scope {bad} may never be granted to a skill-facing token (D3)")

    def allows(self, method: str, path: str) -> tuple[bool, str]:
        for prefix in DENIED_PREFIXES:
            if path.startswith(prefix):
                return False, (f"path {prefix} is outside every granted scope "
                               f"(billing/admin are never granted to a skill)")
        for m, prefix, scope in SCOPE_RULES:
            if path.startswith(prefix) and method.upper() == m:
                if scope in self.scope:
                    return True, scope
                return False, f"scope {scope} not granted"
        return False, f"no scope rule grants {method.upper()} {path}"

    def sign(self, ts: str | None = None) -> dict[str, str]:
        """AK/SK headers the Beehive core accepts (internal/auth/hybrid.go APIKeyAuth)."""
        ts = ts or str(int(time.time()))
        sig = hmac.new(self.secret_key.encode(),
                       (self.access_key + ts).encode(), hashlib.sha256).hexdigest()
        return {"X-Api-Key": self.access_key, "X-Api-Timestamp": ts, "X-Api-Signature": sig}

    @property
    def scope_list(self) -> list[str]:
        return sorted(self.scope)


def skill_token(access_key: str, secret_key: str, account: str = "",
                scope: tuple[str, ...] = DEFAULT_SCOPE) -> RestrictedToken:
    """Mint the per-run token handed to a skill's tools."""
    return RestrictedToken(access_key=access_key, secret_key=secret_key, account=account, scope=scope)
