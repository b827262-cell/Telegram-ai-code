from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from bridge.api import BridgeAPIServer
from bridge.config import Settings


class BridgeCancellationApiTests(unittest.TestCase):
    def test_only_queued_workflow_can_be_cancelled_over_authenticated_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            settings = Settings.from_env(
                {
                    "CODEX_ALLOWED_WORKSPACES": str(workspace),
                    "CODEX_DEFAULT_WORKSPACE": str(workspace),
                    "CODEX_BRIDGE_DATA_DIR": str(root / "state"),
                    "TELEGRAM_ALLOWED_CHAT_ID": "12345",
                },
                root_dir=Path(__file__).resolve().parents[1],
            )
            token = "bridge-test-token-012345678901234567890"
            server = BridgeAPIServer(("127.0.0.1", 0), settings, token)
            workflow, _job = server.queue.submit_workflow(
                chat_id="12345",
                prompt="queued test workflow",
                workspace=workspace,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/workflow/{workflow.id}/cancel"
                request = urllib.request.Request(
                    url,
                    data=json.dumps({"reason": "operator requested"}).encode(),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode())
                self.assertEqual(response.status, 200)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["workflow"]["status"], "failed")
                self.assertEqual(payload["job"]["status"], "failed")
                self.assertIn("CANCELLED_BY_OPERATOR:", payload["workflow"]["error"])

                conflict_request = urllib.request.Request(
                    url,
                    data=b"{}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(conflict_request, timeout=5)
                self.assertEqual(caught.exception.code, 409)
                self.assertEqual(json.loads(caught.exception.read().decode())["code"], "WORKFLOW_STATE_CONFLICT")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


class BridgeWorkflowApiTests(unittest.TestCase):
    def _server(self, root: Path) -> tuple[BridgeAPIServer, str]:
        workspace = root / "workspace"
        workspace.mkdir()
        settings = Settings.from_env(
            {
                "CODEX_ALLOWED_WORKSPACES": str(workspace),
                "CODEX_DEFAULT_WORKSPACE": str(workspace),
                "CODEX_BRIDGE_DATA_DIR": str(root / "state"),
                "TELEGRAM_ALLOWED_CHAT_ID": "12345",
            },
            root_dir=Path(__file__).resolve().parents[1],
        )
        token = "bridge-test-token-012345678901234567890"
        return BridgeAPIServer(("127.0.0.1", 0), settings, token), token

    @staticmethod
    def _request(url: str, token: str, body: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode())

    def test_native_health_contract_and_no_external_write_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server, token = self._server(Path(directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                status, health = self._request(f"{base}/health", token)
                self.assertEqual(status, 200)
                self.assertEqual(health["service"], "codex-bridge")
                self.assertEqual(health["api"], "v1")

                status, smoke = self._request(
                    f"{base}/workflow", token, {"task": "safe smoke", "noExternalWrite": True}
                )
                self.assertEqual(status, 202)
                self.assertTrue(smoke["workflow"]["no_external_write"])
                self.assertFalse(
                    server.queue.get_workflow(smoke["workflow"]["id"]).external_publication_enabled
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_malformed_no_external_write_is_rejected_before_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server, token = self._server(Path(directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/workflow"
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self._request(url, token, {"task": "unsafe ambiguity", "noExternalWrite": "false"})
                self.assertEqual(caught.exception.code, 400)
                self.assertEqual(server.queue.recent_workflows_for_chat("12345"), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
