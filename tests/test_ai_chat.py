import json
from unittest.mock import Mock

import pytest

import ai_chat


def test_ask_sends_dated_chart_summary_without_storing_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps({"output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "測試回答"}]}]}).encode()

    def fake_urlopen(req, timeout):
        seen["request"] = req
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(ai_chat.request, "urlopen", fake_urlopen)
    payload = {"symbol": "MU", "asof": "2026-09-28", "current": 100.0,
               "candles": [{"close": i} for i in range(500)], "zones": [], "signals": []}
    assert ai_chat.ask(payload, [{"role": "user", "content": "趨勢？"}]) == "測試回答"
    sent = json.loads(seen["request"].data)
    assert seen["timeout"] == 30 and sent["store"] is False
    assert "2026-09-28" in sent["instructions"] and "candles" not in sent["instructions"]
    assert sent["input"] == [{"role": "user", "content": "趨勢？"}]


def test_no_answer_is_reported(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setattr(ai_chat.request, "urlopen", Mock(return_value=Mock(
        __enter__=Mock(return_value=Mock(read=Mock(return_value=b'{"output": []}'))),
        __exit__=Mock(return_value=None))))
    with pytest.raises(ai_chat.ChatError, match="沒有回傳文字"):
        ai_chat.ask({"symbol": "MU"}, [{"role": "user", "content": "趨勢？"}])
