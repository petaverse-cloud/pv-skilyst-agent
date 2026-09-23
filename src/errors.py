"""Error hierarchy, kept in one dependency-free module so every layer can raise it.

    SkilystError
      SkillValidationError   a package or manifest rule was violated
        SkillMutationError   the bytes on disk are not the bytes that were installed/received
        ManifestError        a sidecar manifest rule was violated

Callers that want "anything wrong with this package" catch SkillValidationError,
which covers the manifest layer too. Nothing here is ever swallowed: the CLI maps
these to exit code 2 (refused) and the agent loop hands the text to the model.
"""
from __future__ import annotations


class SkilystError(Exception):
    """Base class for every error this runtime raises deliberately."""


class SkillValidationError(SkilystError, ValueError):
    """Raised for anything that must NOT be silently accepted."""


class SkillMutationError(SkillValidationError):
    """The package on disk no longer matches what was installed/received."""


class ManifestError(SkillValidationError):
    """A manifest.json rule was violated."""
