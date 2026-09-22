"""History sanitization preserves tool text while dropping screenshot payloads."""

import json

from workflow import _clamp_tool_results


def test_clamp_preserves_malformed_json_once_without_mutating_history() -> None:
    malformed = {"text": "{not JSON: preserve this tool output"}
    screenshot = {"text": json.dumps({
        "screenshot": "image bytes", "mediaType": "image/png", "note": "keep",
    })}
    content = [malformed, {"image": {"format": "png"}}, screenshot, {"text": "tail"}]
    messages = [{"role": "user", "content": [{"toolResult": {
        "toolUseId": "capture", "content": content,
    }}]}]

    clamped = _clamp_tool_results(messages)

    result = clamped[0]["content"][0]["toolResult"]["content"]
    assert result == [malformed, {"text": json.dumps({"note": "keep"})}, {"text": "tail"}]
    assert messages[0]["content"][0]["toolResult"]["content"] == content
    assert "screenshot" in json.loads(screenshot["text"])