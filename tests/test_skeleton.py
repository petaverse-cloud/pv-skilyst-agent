"""v0.3 workflow_skeleton (#55): manifest parsing, load-time validation, and
the materialization-time gate.

The skeleton is the author's contract (skills-as-product-core.md §3.2, PR #687):
nodes with freedom tiers (pinned / parameterized / free) plus free zones where
the materializing agent may innovate. These tests pin:

  1. the spec's own §3.2 example parses and validates;
  2. structural violations are loud, actionable ManifestErrors;
  3. check_skeleton compares a materialized board against the contract:
     missing pinned slots, config outside config_open, zone overflows,
     undeclared additions -- all blocking, all with actionable text;
  4. official skills that declare no skeleton keep working untouched;
  5. the canvas gate refuses a violating submission BEFORE quoting/charging.
"""
import unittest
from unittest import mock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from errors import ManifestError                       # noqa: E402
from manifest import parse_skeleton, skeleton_of       # noqa: E402
from skills.loader import SkillPackage                 # noqa: E402
from skills.store import check_skeleton                # noqa: E402

SPEC_EXAMPLE = {
    "version": 1,
    "nodes": [
        {"node_id": "generate:minimax-h3", "freedom": "pinned", "role": "hero_shot"},
        {"node_id": "generate:*", "freedom": "parameterized",
         "config_open": ["provider", "resolution", "duration"], "role": "b_roll"},
    ],
    "free_zones": [
        {"name": "transitions", "max_nodes": 3,
         "allowed_node_types": ["process:transcode", "process:subtitle"]},
    ],
    "reference_workflow": {"snapshot_of": "wf-1 abc123"},
}


def _pkg(skeleton: dict | None, skill_id: str = "skilyst/test-skill") -> SkillPackage:
    """A minimal SkillPackage carrying (or not) a workflow_skeleton."""
    from skills.loader import CommunityMeta
    manifest = None
    if skeleton is not None:
        manifest = {"skill_id": skill_id, "workflow_skeleton": skeleton}
    pkg = SkillPackage.__new__(SkillPackage)
    pkg.dir = Path(".")
    pkg.community = CommunityMeta(name="test-skill", description="d", license=None,
                                  compatibility=None, metadata={}, allowed_tools=None, raw={})
    pkg.manifest = manifest
    pkg.digest = "sha256:" + "0" * 64
    pkg.body = ""
    pkg.tree_digest = ""
    pkg.warnings = ()
    return pkg


class SkeletonParseTests(unittest.TestCase):
    def test_spec_example_parses_and_validates(self):
        sk = parse_skeleton(SPEC_EXAMPLE)
        self.assertEqual(sk.version, 1)
        self.assertEqual(len(sk.nodes), 2)
        self.assertEqual(sk.nodes[0].freedom, "pinned")
        self.assertEqual(sk.nodes[0].config_open, [])
        self.assertEqual(sk.nodes[1].config_open, ["provider", "resolution", "duration"])
        self.assertEqual(sk.free_zones[0].max_nodes, 3)
        self.assertEqual(sk.reference_workflow, {"snapshot_of": "wf-1 abc123"})

    def test_wildcard_family_matching(self):
        sk = parse_skeleton(SPEC_EXAMPLE)
        self.assertIsNotNone(sk.matching_node("generate:minimax-h3"))
        self.assertIsNotNone(sk.matching_node("generate:gpt-image-2"))  # via generate:*
        self.assertIsNone(sk.matching_node("process:transcode"))       # not declared

    def test_bad_version_is_loud(self):
        with self.assertRaises(ManifestError) as ctx:
            parse_skeleton({"version": 2, "nodes": []})
        self.assertIn("version must be 1", str(ctx.exception))

    def test_pinned_with_config_open_is_rejected(self):
        with self.assertRaises(ManifestError) as ctx:
            parse_skeleton({"version": 1, "nodes": [
                {"node_id": "generate:x", "freedom": "pinned", "config_open": ["provider"]}]})
        self.assertIn("pinned but declares config_open", str(ctx.exception))

    def test_parameterized_without_config_open_is_rejected(self):
        with self.assertRaises(ManifestError) as ctx:
            parse_skeleton({"version": 1, "nodes": [
                {"node_id": "generate:x", "freedom": "parameterized"}]})
        self.assertIn("no config_open", str(ctx.exception))

    def test_unknown_freedom_is_rejected(self):
        with self.assertRaises(ManifestError) as ctx:
            parse_skeleton({"version": 1, "nodes": [
                {"node_id": "generate:x", "freedom": "wild"}]})
        self.assertIn("pinned|parameterized|free", str(ctx.exception))

    def test_bad_node_id_shape_is_rejected(self):
        with self.assertRaises(ManifestError):
            parse_skeleton({"version": 1, "nodes": [
                {"node_id": "not-an-id", "freedom": "pinned"}]})

    def test_zone_rules(self):
        with self.assertRaises(ManifestError):
            parse_skeleton({"version": 1, "free_zones": [
                {"name": "Not Kebab", "max_nodes": 3}]})
        with self.assertRaises(ManifestError):
            parse_skeleton({"version": 1, "free_zones": [
                {"name": "ok", "max_nodes": -1}]})

    def test_skeleton_of_none_when_undeclared(self):
        self.assertIsNone(skeleton_of({}))
        self.assertIsNone(skeleton_of(None))
        self.assertIsNotNone(skeleton_of({"workflow_skeleton": SPEC_EXAMPLE}))


class SkeletonMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.pkg = _pkg(SPEC_EXAMPLE)

    def test_faithful_materialization_is_clean(self):
        problems = check_skeleton(self.pkg, {"nodes": [
            {"node_id": "generate:minimax-h3", "config": {"prompt": "hero"}},
            {"node_id": "generate:gpt-image-2", "config": {"provider": "gpt-image-2"}},
            {"node_id": "process:transcode", "zone": "transitions"},
        ]})
        self.assertEqual(problems, [])

    def test_missing_pinned_slot_is_blocking(self):
        problems = check_skeleton(self.pkg, {"nodes": [
            {"node_id": "generate:gpt-image-2", "config": {}}]})
        self.assertTrue(problems)
        self.assertTrue(all(p.blocking for p in problems))
        self.assertTrue(any("generate:minimax-h3" in p.detail for p in problems))

    def test_config_outside_config_open_is_blocking(self):
        problems = check_skeleton(self.pkg, {"nodes": [
            {"node_id": "generate:minimax-h3", "config": {"prompt": "x"}},
            {"node_id": "generate:gpt-image-2", "config": {"style": "cinematic"}},
        ]})
        self.assertTrue(any("parameterized on config_open" in p.detail for p in problems))

    def test_zone_overflow_is_blocking(self):
        nodes = [{"node_id": "generate:minimax-h3", "config": {}},
                 {"node_id": "generate:gpt-image-2", "config": {}}]
        nodes += [{"node_id": "process:subtitle", "zone": "transitions"} for _ in range(4)]
        problems = check_skeleton(self.pkg, {"nodes": nodes})
        self.assertTrue(any("max_nodes" in p.detail for p in problems))

    def test_zone_type_violation_is_blocking(self):
        # A node parked in a zone whose type list does not cover it: it matches
        # no slot (process:watermark is not generate:*) and the zone rejects it.
        problems = check_skeleton(self.pkg, {"nodes": [
            {"node_id": "generate:minimax-h3", "config": {}},
            {"node_id": "generate:gpt-image-2", "config": {}},
            {"node_id": "process:watermark", "zone": "transitions"},
        ]})
        self.assertTrue(any(p.blocking for p in problems))

    def test_undeclared_addition_is_blocking(self):
        problems = check_skeleton(self.pkg, {"nodes": [
            {"node_id": "generate:minimax-h3", "config": {}},
            {"node_id": "generate:gpt-image-2", "config": {}},
            {"node_id": "process:watermark"},
        ]})
        self.assertTrue(any("matches no skeleton slot" in p.detail for p in problems))

    def test_no_skeleton_no_problems(self):
        pkg = _pkg(None)
        self.assertEqual(check_skeleton(pkg, {"nodes": [{"node_id": "whatever"}]}), [])


class SkeletonGateTests(unittest.TestCase):
    """The canvas run_workflow gate: refusing BEFORE quote/charge (#55 scope 3)."""

    def _ops(self, pkg):
        from canvas import CanvasOps
        ops = CanvasOps(client=mock.Mock(), session_id="s1",
                        skeleton_gate=lambda nodes: check_skeleton(pkg, {"nodes": nodes}))
        # Board with a missing pinned slot.
        ops.get_workflow = mock.Mock(return_value={
            "nodes": [{"node_id": "generate:gpt-image-2", "config": {}}]})
        return ops

    def test_gate_refuses_a_violating_board_before_submit(self):
        from beehive import BeehiveError
        ops = self._ops(_pkg(SPEC_EXAMPLE))
        with self.assertRaises(BeehiveError) as ctx:
            ops.run_workflow("wf-1")
        self.assertIn("workflow_skeleton", str(ctx.exception))
        # Loud AND actionable: names the missing slot.
        self.assertIn("generate:minimax-h3", str(ctx.exception))
        # Nothing was submitted: the transport was never asked to POST /jobs.
        ops.client.request.assert_not_called()

    def test_gate_passes_a_faithful_board_through(self):
        ops = self._ops(_pkg(SPEC_EXAMPLE))
        ops.get_workflow = mock.Mock(return_value={"nodes": [
            {"node_id": "generate:minimax-h3", "config": {"prompt": "hero"}},
            {"node_id": "generate:gpt-image-2", "config": {}},
            {"node_id": "process:transcode", "zone": "transitions"},
        ]})
        ops.client.request.return_value = (201, {"payload": {"job_id": "j1", "status": "queued"}})
        result = ops.run_workflow("wf-1", quote_first=False)
        self.assertEqual(result["job_id"], "j1")


if __name__ == "__main__":
    unittest.main()
