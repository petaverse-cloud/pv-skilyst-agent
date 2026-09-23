"""Beehive platform integration layer (scope gate + API client)."""
from .client import (ARTIFACT_KEYS, DEFAULT_BASE, TERMINAL_STATUSES, ArtifactCheck, BeehiveClient,
                     BeehiveError, JobHandle, artifact_url, client_for, client_from_bearer, job_handle,
                     verify_artifact)
from .scope import (DEFAULT_SCOPE, DENIED_PREFIXES, FORBIDDEN_SCOPES, SCOPE_RULES, RestrictedToken,
                    ScopeRefusal, skill_token)

__all__ = ["ARTIFACT_KEYS", "DEFAULT_BASE", "TERMINAL_STATUSES", "ArtifactCheck", "BeehiveClient",
           "BeehiveError", "JobHandle", "artifact_url", "client_for", "client_from_bearer", "job_handle",
           "verify_artifact", "DEFAULT_SCOPE", "DENIED_PREFIXES", "FORBIDDEN_SCOPES", "SCOPE_RULES",
           "RestrictedToken", "ScopeRefusal", "skill_token"]
