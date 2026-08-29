from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bridge.config import ConfigurationError, Settings
from bridge.models import SUPPORTED_PROVIDERS
from bridge.sandbox import SANDBOX_MODES
from bridge.workspaces import (
    DEFAULT_WORKSPACE_POLICY,
    LEGACY_WORKSPACE_POLICY,
    SANDBOX_MODE_RANK,
    WORKSPACE_POLICIES,
    WorkspacePolicy,
    WorkspaceRoutingError,
    enforce_route,
    validate_workspace_alias,
    workspace_policy,
)


def routing_settings(
    root: Path,
    *,
    alias_value: str | None = "auto",
    default_alias: str = "meeting-room",
    allow_extra_workspace: bool = True,
) -> Settings:
    """Build a two-workspace deployment. ``alias_value='auto'`` wires both."""

    bridge = root / "bridge"
    meeting_room = root / "meeting-room"
    bridge.mkdir(exist_ok=True)
    meeting_room.mkdir(exist_ok=True)
    workspaces = [bridge, meeting_room]
    if allow_extra_workspace:
        unaliased = root / "unaliased"
        unaliased.mkdir(exist_ok=True)
        workspaces.append(unaliased)
    aliases = alias_value
    if alias_value == "auto":
        aliases = f"bridge={bridge}{':'}meeting-room={meeting_room}"
    env = {
        "CODEX_ALLOWED_WORKSPACES": ":".join(str(item) for item in workspaces),
        "CODEX_DEFAULT_WORKSPACE": str({"bridge": bridge, "meeting-room": meeting_room}[default_alias]),
        "CODEX_BRIDGE_DATA_DIR": str(root / "state"),
        "CODEX_REPORT_SCHEMA": str(Path(__file__).resolve().parents[1] / "schemas" / "codex_report.schema.json"),
        "TELEGRAM_ALLOWED_CHAT_ID": "12345",
    }  # fmt: skip
    if aliases:
        env["CODEX_WORKSPACE_ALIASES"] = aliases
    return Settings.from_env(env, root_dir=Path(__file__).resolve().parents[1])


class WorkspaceAliasTokenTests(unittest.TestCase):
    def test_a_filesystem_path_is_never_a_valid_alias(self) -> None:
        rejected = [
            "/home/dev/project/gpt-codex-bridge",
            "../gpt-codex-bridge",
            "../../etc/passwd",
            "..",
            ".",
            "~/gpt-codex-bridge",
            "gpt-codex-bridge/../ai-meeting-room",
            "./bridge",
            "bridge/../../outside",
            "%2e%2e%2fgpt-codex-bridge",
            "..\\gpt-codex-bridge",
            "bridge ai-meeting-room",
            "BRIDGE/",
            "workspace=/tmp/evil",
            "-bridge",
            "bridgé",
            "bridge\nsub",
            "x" * 33,
        ]
        for value in rejected:
            with self.subTest(alias=value):
                with self.assertRaises(WorkspaceRoutingError) as caught:
                    validate_workspace_alias(value)
                self.assertEqual(caught.exception.code, "WORKSPACE_ALIAS_INVALID")

    def test_blank_and_non_string_aliases_fail_closed(self) -> None:
        for value in ("", "   ", None, 123, True, ["bridge"]):
            with self.subTest(alias=repr(value)):
                with self.assertRaises(WorkspaceRoutingError) as caught:
                    validate_workspace_alias(value)
                self.assertEqual(caught.exception.code, "WORKSPACE_ALIAS_INVALID")

    def test_declared_aliases_are_accepted_case_insensitively(self) -> None:
        for value in ("bridge", "BRIDGE", " meeting-room "):
            with self.subTest(alias=value):
                self.assertEqual(validate_workspace_alias(value), value.strip().lower())

    def test_a_single_character_slug_is_a_valid_alias_shape(self) -> None:
        self.assertEqual(validate_workspace_alias("b"), "b")


class WorkspacePolicyTableTests(unittest.TestCase):
    def test_sandbox_rank_ordering_covers_every_supported_mode(self) -> None:
        self.assertEqual(set(SANDBOX_MODE_RANK), set(SANDBOX_MODES))
        self.assertLess(
            SANDBOX_MODE_RANK["read-only"], SANDBOX_MODE_RANK["workspace-write"]
        )
        self.assertLess(
            SANDBOX_MODE_RANK["workspace-write"], SANDBOX_MODE_RANK["danger-full-access"]
        )

    def test_unreviewed_alias_gets_the_restrictive_floor(self) -> None:
        self.assertEqual(workspace_policy("some-new-alias"), DEFAULT_WORKSPACE_POLICY)
        self.assertEqual(DEFAULT_WORKSPACE_POLICY.max_sandbox_mode, "read-only")
        self.assertFalse(DEFAULT_WORKSPACE_POLICY.publication_allowed)

    def test_bridge_policy_caps_write_without_publication(self) -> None:
        policy = workspace_policy("bridge")
        self.assertEqual(policy.max_sandbox_mode, "workspace-write")
        self.assertFalse(policy.publication_allowed)

    def test_meeting_room_policy_is_the_pre_routing_ceiling(self) -> None:
        self.assertEqual(workspace_policy("meeting-room"), LEGACY_WORKSPACE_POLICY)
        self.assertEqual(LEGACY_WORKSPACE_POLICY.max_sandbox_mode, "danger-full-access")
        self.assertTrue(LEGACY_WORKSPACE_POLICY.publication_allowed)

    def test_every_shipped_policy_names_only_supported_providers(self) -> None:
        for alias, policy in WORKSPACE_POLICIES.items():
            with self.subTest(alias=alias):
                self.assertTrue(policy.allowed_providers)
                self.assertTrue(policy.allowed_providers <= SUPPORTED_PROVIDERS)

    def test_a_policy_naming_an_unknown_provider_is_rejected_at_construction(self) -> None:
        with self.assertRaises(ValueError) as caught:
            WorkspacePolicy(
                max_sandbox_mode="read-only",
                allowed_providers=frozenset({"codex", "not-a-provider"}),
                publication_allowed=False,
            )
        self.assertIn("not-a-provider", str(caught.exception))

    def test_provider_denial_is_enforced_from_the_policy(self) -> None:
        narrow = WorkspacePolicy(
            max_sandbox_mode="workspace-write",
            allowed_providers=frozenset({"codex"}),
            publication_allowed=True,
        )
        with self.assertRaises(WorkspaceRoutingError) as caught:
            enforce_route(
                alias="narrow",
                policy=narrow,
                provider="agy",
                sandbox_mode="read-only",
                external_publication_requested=False,
            )
        self.assertEqual(caught.exception.code, "WORKSPACE_PROVIDER_DENIED")
        enforce_route(
            alias="narrow",
            policy=narrow,
            provider="codex",
            sandbox_mode="workspace-write",
            external_publication_requested=True,
        )

    def test_a_permissive_policy_never_raises_a_lower_mode(self) -> None:
        enforce_route(
            alias="permissive",
            policy=LEGACY_WORKSPACE_POLICY,
            provider="codex",
            sandbox_mode="read-only",
            external_publication_requested=True,
        )


class SettingsAliasLoadTests(unittest.TestCase):
    def test_alias_target_outside_the_allowlist_refuses_to_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            with self.assertRaises(ConfigurationError) as caught:
                routing_settings(root, alias_value=f"bridge={outside}")
            self.assertIn("not in CODEX_ALLOWED_WORKSPACES", str(caught.exception))

    def test_alias_entry_without_a_target_refuses_to_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ConfigurationError) as caught:
                routing_settings(root, alias_value="bridge")
            self.assertIn("alias=/absolute/path", str(caught.exception))

    def test_duplicate_alias_refuses_to_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ConfigurationError) as caught:
                routing_settings(root, alias_value=f"bridge={root / 'bridge'}:bridge={root / 'meeting-room'}")  # fmt: skip
            self.assertIn("duplicate alias", str(caught.exception))

    def test_symlink_alias_cannot_escape_the_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside-target"
            outside.mkdir()
            disguised = root / "looks-internal"
            disguised.symlink_to(outside)
            with self.assertRaises(ConfigurationError) as caught:
                routing_settings(root, alias_value=f"bridge={disguised}")
            self.assertIn("not in CODEX_ALLOWED_WORKSPACES", str(caught.exception))

    def test_alias_target_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ConfigurationError):
                routing_settings(root, alias_value=f"bridge={root / 'never-created'}")

    def test_relative_alias_target_refuses_to_load(self) -> None:
        # Path.resolve() would happily turn this into a cwd-relative directory,
        # so the boundary has to be checked before resolving.
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigurationError) as caught:
                routing_settings(Path(directory), alias_value="bridge=./bridge")
            self.assertIn("must be absolute", str(caught.exception))
            with self.assertRaises(ConfigurationError) as parent:
                routing_settings(Path(directory), alias_value="bridge=../outside")
            self.assertIn("must be absolute", str(parent.exception))

    def test_invalid_alias_token_refuses_to_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ConfigurationError) as caught:
                routing_settings(root, alias_value=f"Bridge!={root / 'bridge'}")
            self.assertIn("CODEX_WORKSPACE_ALIASES", str(caught.exception))

    def test_deployment_without_aliases_still_loads_and_routes_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = routing_settings(Path(directory), alias_value=None)
            self.assertEqual(settings.workspace_aliases, {})
            route = settings.resolve_workspace_route(
                provider="codex", sandbox_mode="workspace-write"
            )
            self.assertEqual(route.workspace, settings.default_workspace)
            self.assertEqual(route.policy, LEGACY_WORKSPACE_POLICY)
            self.assertEqual(route.alias, "")


class WorkspaceRouteResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.settings = routing_settings(self.root)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_known_aliases_resolve_to_their_exact_paths(self) -> None:
        expected = {"bridge": "bridge", "meeting-room": "meeting-room"}
        for alias, directory_name in expected.items():
            with self.subTest(alias=alias):
                route = self.settings.resolve_workspace_route(
                    provider="codex", sandbox_mode="read-only", alias=alias
                )
                self.assertEqual(route.alias, alias)
                self.assertEqual(route.workspace, (self.root / directory_name).resolve())

    def test_similar_prefixed_alias_is_unknown_not_a_path_match(self) -> None:
        with self.assertRaises(WorkspaceRoutingError) as caught:
            self.settings.resolve_workspace_route(
                provider="codex", sandbox_mode="read-only", alias="bridge-evil"
            )
        self.assertEqual(caught.exception.code, "WORKSPACE_ALIAS_UNKNOWN")

    def test_refusal_messages_never_disclose_absolute_paths(self) -> None:
        cases = [
            ("WORKSPACE_ALIAS_UNKNOWN", "nope", "workspace-write", False),
            ("WORKSPACE_ALIAS_INVALID", "/home/dev/secret", "workspace-write", False),
            ("WORKSPACE_SANDBOX_DENIED", "bridge", "danger-full-access", False),
            ("WORKSPACE_PUBLICATION_DENIED", "bridge", "workspace-write", True),
        ]
        for code, alias, mode, publication in cases:
            with self.subTest(code=code):
                with self.assertRaises(WorkspaceRoutingError) as caught:
                    self.settings.resolve_workspace_route(
                        provider="codex",
                        sandbox_mode=mode,
                        external_publication_requested=publication,
                        alias=alias,
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn(str(self.root), str(caught.exception))

    def test_bridge_accepts_read_only_and_workspace_write(self) -> None:
        for mode in ("read-only", "workspace-write"):
            with self.subTest(mode=mode):
                route = self.settings.resolve_workspace_route(
                    provider="codex", sandbox_mode=mode, alias="bridge"
                )
                self.assertEqual(route.workspace, (self.root / "bridge").resolve())

    def test_bridge_rejects_a_mode_above_its_ceiling(self) -> None:
        with self.assertRaises(WorkspaceRoutingError) as caught:
            self.settings.resolve_workspace_route(
                provider="codex", sandbox_mode="danger-full-access", alias="bridge"
            )
        self.assertEqual(caught.exception.code, "WORKSPACE_SANDBOX_DENIED")

    def test_meeting_room_keeps_the_highest_mode_it_had_before_routing(self) -> None:
        route = self.settings.resolve_workspace_route(
            provider="codex", sandbox_mode="danger-full-access", alias="meeting-room"
        )
        self.assertEqual(route.workspace, (self.root / "meeting-room").resolve())

    def test_bridge_denies_requested_external_publication(self) -> None:
        with self.assertRaises(WorkspaceRoutingError) as caught:
            self.settings.resolve_workspace_route(
                provider="codex",
                sandbox_mode="workspace-write",
                external_publication_requested=True,
                alias="bridge",
            )
        self.assertEqual(caught.exception.code, "WORKSPACE_PUBLICATION_DENIED")

    def test_meeting_room_allows_requested_external_publication(self) -> None:
        route = self.settings.resolve_workspace_route(
            provider="codex",
            sandbox_mode="workspace-write",
            external_publication_requested=True,
            alias="meeting-room",
        )
        self.assertTrue(route.policy.publication_allowed)

    def test_omitted_alias_keeps_the_default_route(self) -> None:
        route = self.settings.resolve_workspace_route(
            provider="codex", sandbox_mode="danger-full-access"
        )
        self.assertEqual(route.workspace, self.settings.default_workspace)
        self.assertEqual(route.policy, LEGACY_WORKSPACE_POLICY)
        self.assertEqual(route.alias, "meeting-room")

    def test_default_route_inherits_the_cap_of_the_workspace_it_lands_on(self) -> None:
        # If an operator ever points CODEX_DEFAULT_WORKSPACE at the bridge, the
        # default route must not silently keep full access and publication.
        settings = routing_settings(self.root, default_alias="bridge")
        with self.assertRaises(WorkspaceRoutingError) as caught:
            settings.resolve_workspace_route(
                provider="codex", sandbox_mode="danger-full-access"
            )
        self.assertEqual(caught.exception.code, "WORKSPACE_SANDBOX_DENIED")
        with self.assertRaises(WorkspaceRoutingError) as publication:
            settings.resolve_workspace_route(
                provider="codex",
                sandbox_mode="workspace-write",
                external_publication_requested=True,
            )
        self.assertEqual(publication.exception.code, "WORKSPACE_PUBLICATION_DENIED")
        self.assertEqual(
            settings.resolve_workspace_route(
                provider="codex",
                sandbox_mode="workspace-write",
                external_publication_requested=False,
            ).alias,
            "bridge",
        )

    def test_an_unaliased_allowlisted_workspace_is_not_reachable_by_alias(self) -> None:
        self.assertEqual(self.settings.workspace_aliases.get("unaliased"), None)
        with self.assertRaises(WorkspaceRoutingError) as caught:
            self.settings.resolve_workspace_route(
                provider="codex", sandbox_mode="read-only", alias="unaliased"
            )
        self.assertEqual(caught.exception.code, "WORKSPACE_ALIAS_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
