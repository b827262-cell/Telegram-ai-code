"""Structured bridge logging must be useful without ever leaking secrets."""

from __future__ import annotations

import json
import logging

import pytest

from bridge.observability import (
    LOGGER_NAME,
    get_logger,
    log_event,
    safe_id,
    safe_path,
    safe_error_metadata,
)


BEARER = "s3cret-bearer-token-value-abcdefghijklmnop"
BOT_TOKEN = "123456789:AAHscrubMeCompletelyPleaseXYZ"
CHAT_ID = "987654321"


def parse_lines(caplog: pytest.LogCaptureFixture) -> list[dict]:
    events = []
    for record in caplog.records:
        if record.name == LOGGER_NAME:
            events.append(json.loads(record.getMessage()))
    return events


class TestSafeId:
    @pytest.mark.parametrize(
        "value",
        ["job-fa21371ea80a4e3a", "flow-f81c19393dba461c"],
    )
    def test_accepts_generated_ids(self, value: str) -> None:
        assert safe_id(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "job-../../etc/passwd",
            f"job-{BEARER}",
            "Bearer abc",
            "",
            None,
            12345,
            "job-NOTHEX",
        ],
    )
    def test_rejects_anything_else(self, value: object) -> None:
        assert safe_id(value) is None


class TestSafePath:
    @pytest.mark.parametrize("path", ["/health", "/status", "/run", "/workflow"])
    def test_allowlisted_paths_pass_through(self, path: str) -> None:
        assert safe_path(path) == path

    def test_identifier_segments_are_collapsed(self) -> None:
        assert safe_path("/result/job-fa21371ea80a4e3a") == "/result/<id>"
        assert safe_path("/workflow/flow-f81c19393dba461c") == "/workflow/<id>"

    def test_unknown_paths_are_not_echoed(self) -> None:
        assert safe_path(f"/evil?token={BEARER}") == "<other>"
        assert safe_path("/../../secret") == "<other>"
        assert safe_path(None) == "<invalid>"


class TestSafeErrorMetadata:
    def test_reports_class_without_rendering_message(self) -> None:
        exc = ValueError(f"request prompt and Bearer {BEARER}")
        result = safe_error_metadata(exc)
        assert result["error_class"] == "ValueError"
        assert "error_message" not in result
        assert BEARER not in json.dumps(result)


class TestLogEvent:
    def test_emits_json_with_allowlisted_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            log_event(
                get_logger(),
                "workflow_dispatched",
                workflow_id="flow-f81c19393dba461c",
                job_id="job-fa21371ea80a4e3a",
                request_body=BEARER,
            )

        (event,) = parse_lines(caplog)
        assert event["event"] == "workflow_dispatched"
        assert event["workflow_id"] == "flow-f81c19393dba461c"
        assert event["job_id"] == "job-fa21371ea80a4e3a"
        assert "request_body" not in event
        assert BEARER not in caplog.text

    def test_none_fields_are_dropped(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            log_event(get_logger(), "http_response", code=None, status=200)

        (event,) = parse_lines(caplog)
        assert "code" not in event
        assert event["status"] == 200

    def test_unknown_fields_and_events_are_dropped(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            log_event(get_logger(), "run_dispatched", job_id=None, request_body=BEARER)
            log_event(get_logger(), BEARER, status=200)

        (event,) = parse_lines(caplog)
        assert event == {"event": "run_dispatched"}
        assert BEARER not in caplog.text

    def test_stack_frames_preserve_diagnostics_without_exception_text(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            try:
                raise RuntimeError("queue exploded")
            except RuntimeError as exc:
                log_event(
                    get_logger(),
                    "bridge_error",
                    level=logging.ERROR,
                    include_traceback=True,
                    error_message=f"prompt {BEARER}",
                    **safe_error_metadata(exc),
                )

        record = next(r for r in caplog.records if r.name == LOGGER_NAME)
        event = json.loads(record.getMessage())
        assert event["error_class"] == "RuntimeError"
        # Only code locations survive; exception text/source/locals do not.
        assert "traceback_frames" in event
        assert any(frame["file"] == __file__.split("/")[-1] for frame in event["traceback_frames"])
        assert "queue exploded" not in record.getMessage()
        assert BEARER not in record.getMessage()


class TestNoSecretLogging:
    """No helper may emit a credential even when handed one directly."""

    def test_secrets_never_survive_into_a_log_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            log_event(
                get_logger(),
                "codex_exec_running_rejected",
                path=safe_path(f"/workflow?token={BEARER}"),
                job_id=safe_id(f"job-{BOT_TOKEN}"),
                request_body=f"prompt {BEARER}",
                authorization=f"Bearer {BOT_TOKEN}",
                **safe_error_metadata(ValueError(f"bad {BEARER} and {BOT_TOKEN}")),
            )

        text = caplog.text
        assert BEARER not in text
        assert BOT_TOKEN not in text

    def test_authorization_header_shape_is_redacted_by_settings(self, tmp_path) -> None:
        """A real Settings must scrub bearer/bot-token shapes and the chat id."""

        from pathlib import Path

        from bridge.config import Settings

        workspace = tmp_path / "allowed-repo"
        workspace.mkdir()
        settings = Settings.from_env(
            {
                "CODEX_ALLOWED_WORKSPACES": str(workspace),
                "CODEX_DEFAULT_WORKSPACE": str(workspace),
                "CODEX_BRIDGE_DATA_DIR": str(tmp_path / "state"),
                "TELEGRAM_ALLOWED_CHAT_ID": CHAT_ID,
                "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            },
            root_dir=Path(__file__).resolve().parents[1],
        )

        assert BEARER not in settings.redact_text(f"Authorization: Bearer {BEARER}")
        assert BOT_TOKEN not in settings.redact_text(f"sending via {BOT_TOKEN}")


class TestBridgeErrorHttpContract:
    """A generic failure stays opaque to the client but loud in the log."""

    def test_bridge_error_is_opaque_to_client_and_traced_server_side(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import threading
        import urllib.error
        import urllib.request
        from pathlib import Path

        from bridge.api import BridgeAPIServer
        from bridge.config import Settings

        workspace = tmp_path / "allowed-repo"
        workspace.mkdir()
        settings = Settings.from_env(
            {
                "CODEX_ALLOWED_WORKSPACES": str(workspace),
                "CODEX_DEFAULT_WORKSPACE": str(workspace),
                "CODEX_BRIDGE_DATA_DIR": str(tmp_path / "state"),
                "TELEGRAM_ALLOWED_CHAT_ID": CHAT_ID,
                "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            },
            root_dir=Path(__file__).resolve().parents[1],
        )
        token = "t" * 40
        server = BridgeAPIServer(("127.0.0.1", 0), settings, token)

        # Force an unexpected (non-ValueError) failure inside dispatch.
        # The exception embeds credentials in the two shapes that realistically
        # appear in upstream errors: a bot token, and a labelled bearer header.
        def failing_submit_workflow(*args: object, **kwargs: object) -> None:
            raise RuntimeError(
                f"upstream refused: Authorization: Bearer {BEARER} for {BOT_TOKEN}"
            )

        server.queue.submit_workflow = failing_submit_workflow  # type: ignore[method-assign]

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/workflow",
                data=json.dumps({"task": "do a thing"}).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
                with pytest.raises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=10)

            body = json.loads(caught.value.read().decode())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        # Client contract: opaque code, no traceback, no secret.
        assert caught.value.code == 500
        assert body == {"ok": False, "code": "BRIDGE_ERROR"}
        assert "RuntimeError" not in json.dumps(body)
        assert BEARER not in json.dumps(body)

        # Server contract: diagnosable exception class and structured code
        # locations survive, including both the HTTP call site and the actual
        # monkeypatched failure function.
        bridge_events = [event for event in parse_lines(caplog) if event["event"] == "bridge_error"]
        assert len(bridge_events) == 1
        bridge_event = bridge_events[0]
        assert bridge_event["error_class"] == "RuntimeError"
        frames = bridge_event["traceback_frames"]
        assert all(set(frame) == {"file", "line", "function"} for frame in frames)
        assert any(
            frame["file"] == "api.py" and frame["function"] == "do_POST"
            for frame in frames
        )
        assert any(
            frame["file"] == "test_api_logging.py"
            and frame["function"] == "failing_submit_workflow"
            for frame in frames
        )
        # ...but no exception message/request prompt and no credential appears.
        assert "upstream refused" not in caplog.text
        assert "do a thing" not in caplog.text
        assert BEARER not in caplog.text
        assert token not in caplog.text
        assert BOT_TOKEN not in caplog.text
