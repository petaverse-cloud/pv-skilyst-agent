"""Manifest sidecar layer (extension) of the dual-file skill package format."""
from errors import ManifestError
from .spec import (DIGEST_RE, NodeRequirement, deprecation_warnings, is_hot_update, node_requirements,
                   parse_node_requirement, validate_manifest)

__all__ = ["DIGEST_RE", "ManifestError", "NodeRequirement", "deprecation_warnings",
           "is_hot_update", "node_requirements", "parse_node_requirement", "validate_manifest"]
