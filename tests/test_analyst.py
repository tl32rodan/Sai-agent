from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sai.analyst import (
    SYSTEM_PROMPT, AnalystError, ask, build_payload, gather_context,
)
from tests.conftest import T0


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("no_proxy", "*")


def cmd_record(t=T0, cmd="make lens", exit=2, fp="f" * 12, tail=(), cwd="/home/b/x"):
    return {
        "t": t, "type": "cmd", "cmd": cmd, "cwd": cwd,
        "exit": exit, "dur_s": 1.0, "pane": "%1", "tail": list(tail), "fp": fp,
    }


class TestGatherContext:
    def test_empty_records(self):
        assert gather_context([]) == ""
        assert gather_context([{"t": T0, "type": "pull"}]) == ""

    def test_last_five_commands_only(self):
        records = [cmd_record(t=T0 + i, cmd=f"cmd{i}", exit=0) for i in range(7)]
        context = gather_context(records)
        assert "cmd0" not in context and "cmd1" not in context
        for i in range(2, 7):
            assert f"cmd{i}" in context

    def test_includes_cwd_tail_and_exit(self):
        context = gather_context([cmd_record(tail=["make: *** Error 2"])])
        assert "cwd: /home/b/x" in context
        assert "$ make lens" in context
        assert "exit 2" in context
        assert "make: *** Error 2" in context

    def test_fingerprint_repeat_count_surfaces(self):
        records = [cmd_record(t=T0 + i) for i in range(3)]
        assert "seen 3×" in gather_context(records)

    def test_no_repeat_note_for_single_failure(self):
        assert "seen" not in gather_context([cmd_record()])

    def test_verdict_and_blind_records_ignored(self):
        records = [
            cmd_record(),
            {"t": T0, "type": "verdict", "verdict": "push", "text": "sai ▸ x"},
            {"t": T0, "type": "blind", "pane": "%1", "state": "enter"},
        ]
        context = gather_context(records)
        assert "sai ▸" not in context and "blind" not in context


class TestBuildPayload:
    def test_shape_and_system_prompt(self):
        payload = build_payload("cwd: /x", model="qwen2.5-coder")
        assert payload["model"] == "qwen2.5-coder"
        roles = [m["role"] for m in payload["messages"]]
        assert roles == ["system", "user"]
        assert payload["messages"][0]["content"] == SYSTEM_PROMPT

    def test_planted_aws_secret_never_reaches_payload(self):
        # M0.b acceptance test (PLAN.md §14)
        secret = "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"
        context = gather_context([
            cmd_record(cmd=f"export AWS_SECRET_ACCESS_KEY={secret}", exit=0,
                       tail=[f"AWS_SECRET_ACCESS_KEY={secret}"]),
        ])
        assert secret in context  # gathered…
        payload = build_payload(context, model="m")
        assert secret not in json.dumps(payload)  # …but never on the wire

    def test_context_present_in_user_message(self):
        payload = build_payload("$ make lens   [exit 2]", model="m")
        assert "$ make lens" in payload["messages"][1]["content"]


class _Handler(BaseHTTPRequestHandler):
    response: dict = {}
    seen: list[bytes] = []

    def do_POST(self):
        _Handler.seen.append(self.rfile.read(int(self.headers["Content-Length"])))
        body = json.dumps(_Handler.response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if "/truncate-me" in self.path:  # ask() appends /v1/chat/completions
            # advertise more than we send, then close: IncompleteRead client-side
            self.send_header("Content-Length", str(len(body) + 50))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.connection.close()
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def endpoint():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


class TestAsk:
    def test_happy_path(self, endpoint):
        _Handler.response = {"choices": [{"message": {"content": "  try make -n  "}}]}
        _Handler.seen = []
        reply = ask(endpoint, build_payload("ctx", model="m"), timeout_s=5)
        assert reply == "try make -n"
        wire = json.loads(_Handler.seen[0])
        assert wire["model"] == "m" and len(wire["messages"]) == 2

    def test_unexpected_shape_is_one_line_error(self, endpoint):
        _Handler.response = {"unexpected": True}
        with pytest.raises(AnalystError, match="unexpected response shape"):
            ask(endpoint, {"model": "m", "messages": []}, timeout_s=5)

    def test_unreachable_endpoint_is_one_line_error(self):
        with pytest.raises(AnalystError, match="unreachable"):
            ask("http://127.0.0.1:9", {"model": "m", "messages": []}, timeout_s=2)

    def test_proxy_env_is_ignored(self, endpoint, monkeypatch):
        # context goes only to the configured endpoint — never through a
        # generic proxy from the environment
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        _Handler.response = {"choices": [{"message": {"content": "ok"}}]}
        assert ask(endpoint, build_payload("ctx", model="m"), timeout_s=5) == "ok"

    def test_connection_dropped_mid_body_is_one_line_error(self, endpoint):
        with pytest.raises(AnalystError, match="connection failed"):
            ask(endpoint + "/truncate-me", {"model": "m", "messages": []}, timeout_s=5)
