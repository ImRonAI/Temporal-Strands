"""The agent prompt states that a turn's tool and skill selection wins."""

from __future__ import annotations

import json
from pathlib import Path

AGENT_JSON = Path(__file__).resolve().parents[1] / "agent.json"

SELECTION_SENTENCE = (
    "When the turn lists selected tools and skills, those names are the ones "
    "this turn may call, and a selected discoverable tool is still loaded with "
    "load_tool before it is called."
)


def test_prompt_states_turn_selection_wins() -> None:
    data = json.loads(AGENT_JSON.read_text(encoding="utf-8"))
    assert set(data) == {"name", "prompt"}
    assert data["prompt"].count(SELECTION_SENTENCE) == 1


def test_selection_sentence_follows_the_session_registry_paragraph() -> None:
    prompt = json.loads(AGENT_JSON.read_text(encoding="utf-8"))["prompt"]
    registry = prompt.index("## Already on the session")
    server_side = prompt.index("## Server-side, on the model request")
    assert registry < prompt.index(SELECTION_SENTENCE) < server_side
