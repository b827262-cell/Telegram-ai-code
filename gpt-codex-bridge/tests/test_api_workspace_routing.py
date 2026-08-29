from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from bridge.api import BridgeAPIServer
from bridge.config import Settings

TOKEN = "workspace-routing-test-token-0123456789"
SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "codex_report.schema.json"


class WorkspaceRoutingApiTests(unittest.TestCase):
    """Exercises the HTTP boundary: only opaque aliases may name a workspace."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        bridge = self.root / "bridge"
        meeting_room = self.root / "meeting-room"
        bridge.mkdir()
        meeting_room.mkdir()
        self.bridge = bridge.resolve()
        self.meeting_room = meeting_room.resolve()
        self.settings = Settings.from_env(
            {
                "CODEX_ALLOWED_WORKSPACES": os.pathsep.join([str(self.meeting_room), str(self.bridge)]),
                "CODEX_DEFAULT_WORKSPACE": str(self.meeting_room),
                "CODEX_WORKSPACE_ALIASES": os.pathsep.join(
                    [
                        f"meeting-room={self.meeting_room}",
                        f"bridge={self.bridge}",
                    ]
                ),
                "CODEX_BRIDGE_DATA_DIR": str(self.root / "state"),
                "CODEX_REPORT_SCHEMA": str(SCHEMA),
                "TELEGRAM_ALLOWED_CHAT_ID": "12345",
            },  # fmt: skip
            root_dir=Path(__file__).resolve().parents[1],
        )
        self.server = BridgeAPIServer(("127.0.0.1", 0), self.settings, TOKEN)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
        self._directory.cleanup()

    def _post(self, endpoint: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_address[1]}{endpoint}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            method="POST",
        )  # fmt: skip
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode())

    def test_bridge_alias_dispatches_to_the_bridge_path(self) -> None:
        status, payload = self._post(
            "/run", {"task": "inspect the bridge", "mode": "read", "workspace": "bridge"}
        )
        self.assertEqual(status, 202)
        job = self.server.queue.get(str(payload["job"]["id"]))
        assert job is not None
        self.assertEqual(job.workspace, self.bridge)
        self.assertEqual(job.sandbox_mode, "read-only")

    def test_workspace_alias_is_case_insensitive_on_the_wire(self) -> None:
        status, payload = self._post(
            "/run", {"task": "inspect", "mode": "read", "workspace": "BRIDGE"}
        )
        self.assertEqual(status, 202)
        job = self.server.queue.get(str(payload["job"]["id"]))
        assert job is not None
        self.assertEqual(job.workspace, self.bridge)

    def test_omitted_workspace_keeps_the_pre_routing_default(self) -> None:
        status, payload = self._post("/run", {"task": "unchanged", "mode": "write"})
        self.assertEqual(status, 202)
        job = self.server.queue.get(str(payload["job"]["id"]))
        assert job is not None
        self.assertEqual(job.workspace, self.meeting_room)

    def test_meeting_room_alias_matches_the_default_route(self) -> None:
        status, payload = self._post(
            "/run", {"task": "meeting", "mode": "write", "workspace": "meeting-room"}
        )
        self.assertEqual(status, 202)
        job = self.server.queue.get(str(payload["job"]["id"]))
        assert job is not None
        self.assertEqual(job.workspace, self.meeting_room)

    def test_unknown_alias_is_rejected_without_a_job(self) -> None:
        before = self.server.queue.counts()
        status, payload = self._post(
            "/run", {"task": "nope", "mode": "read", "workspace": "not-configured"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "WORKSPACE_ALIAS_UNKNOWN")
        self.assertFalse(payload["ok"])
        self.assertEqual(self.server.queue.counts(), before)

    def test_absolute_path_input_is_rejected_before_resolution(self) -> None:
        for value in (str(self.bridge), f"{self.bridge}/..", "../bridge", "~/bridge", "", "   "):
            with self.subTest(workspace=value):
                before = self.server.queue.counts()
                status, payload = self._post(
                    "/run", {"task": "escape", "mode": "read", "workspace": value}
                )
                self.assertEqual(status, 400)
                self.assertEqual(payload["code"], "WORKSPACE_ALIAS_INVALID")
                self.assertEqual(self.server.queue.counts(), before)

    def test_bridge_cannot_request_a_mode_above_its_ceiling(self) -> None:
        before = self.server.queue.counts()
        status, payload = self._post(
            "/run", {"task": "escalate", "mode": "full", "workspace": "bridge"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "WORKSPACE_SANDBOX_DENIED")
        self.assertEqual(self.server.queue.counts(), before)

    def test_meeting_room_still_accepts_the_highest_mode(self) -> None:
        status, payload = self._post(
            "/run", {"task": "legacy full", "mode": "full", "workspace": "meeting-room"}
        )
        self.assertEqual(status, 202)
        job = self.server.queue.get(str(payload["job"]["id"]))
        assert job is not None
        self.assertEqual(job.sandbox_mode, "danger-full-access")

    def test_workflow_on_bridge_requires_no_external_write(self) -> None:
        before = self.server.queue.counts()
        status, payload = self._post("/workflow", {"task": "edit bridge", "workspace": "bridge"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "WORKSPACE_PUBLICATION_DENIED")
        self.assertEqual(self.server.queue.counts(), before)

    def test_workflow_on_bridge_with_containment_persists_denied_publication(self) -> None:
        status, payload = self._post(
            "/workflow",
            {"task": "edit bridge", "workspace": "bridge", "noExternalWrite": True},
        )
        self.assertEqual(status, 202)
        workflow = self.server.queue.get_workflow(str(payload["workflow"]["id"]))
        assert workflow is not None
        self.assertEqual(workflow.workspace, self.bridge)
        self.assertFalse(workflow.external_publication_enabled)
        self.assertEqual(workflow.status, "queued")

    def test_workflow_without_alias_keeps_publication_available(self) -> None:
        status, payload = self._post("/workflow", {"task": "legacy workflow"})
        self.assertEqual(status, 202)
        workflow = self.server.queue.get_workflow(str(payload["workflow"]["id"]))
        assert workflow is not None
        self.assertEqual(workflow.workspace, self.meeting_room)
        self.assertTrue(workflow.external_publication_enabled)

    def test_explicit_no_external_write_still_wins_on_meeting_room(self) -> None:
        status, payload = self._post(
            "/workflow", {"task": "contained", "workspace": "meeting-room", "noExternalWrite": True}
        )  # fmt: skip
        self.assertEqual(status, 202)
        workflow = self.server.queue.get_workflow(str(payload["workflow"]["id"]))
        assert workflow is not None
        self.assertFalse(workflow.external_publication_enabled)

    def test_responses_never_echo_the_resolved_workspace_path(self) -> None:
        _, accepted = self._post(
            "/run", {"task": "read bridge", "mode": "read", "workspace": "bridge"}
        )
        self.assertNotIn(str(self.bridge), json.dumps(accepted))
        _, denied = self._post(
            "/run", {"task": "read bridge", "mode": "read", "workspace": "unknown-alias"}
        )
        self.assertNotIn(str(self.bridge), json.dumps(denied))

    def test_invalid_provider_is_still_rejected_on_a_routed_request(self) -> None:
        status, payload = self._post(
            "/run", {"task": "bad provider", "provider": "imagemodel", "workspace": "bridge"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "REQUEST_INVALID")

    def test_unauthenticated_request_is_rejected_before_routing(self) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_address[1]}/run",
            data=json.dumps({"task": "x", "workspace": "bridge"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )  # fmt: skip
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(json.loads(caught.exception.read().decode())["code"], "BRIDGE_UNAUTHORIZED")


if __name__ == "__main__":
    unittest.main()
