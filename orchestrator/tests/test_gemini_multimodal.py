"""Pure turn content-block assembly and Gemini video Blob formatting."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gemini_model import GeminiModel, maps_from_grounding, search_from_grounding
from workflow import (
    TurnDocument,
    TurnImage,
    TurnInput,
    TurnVideo,
    turn_content_blocks,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high"])
async def test_reasoning_effort_maps_to_thinking_level_without_mutating_defaults(monkeypatch, effort):
    model = GeminiModel(
        client_args={"api_key": "test"}, model_id="gemini-3.8-flash",
        params={"thinking_config": {"thinking_level": "high", "include_thoughts": True}},
    )

    async def empty_stream():
        return
        yield

    generate = AsyncMock(side_effect=lambda **kwargs: empty_stream())
    monkeypatch.setattr(model, "_get_client", lambda: SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=generate))
    ))
    _ = [event async for event in model.stream([], invocation_state={"reasoning_effort": effort})]
    thinking = generate.call_args.kwargs["config"]["thinking_config"]
    assert thinking["thinking_level"].lower() == effort
    assert thinking["include_thoughts"] is True
    assert model.config["params"]["thinking_config"]["thinking_level"] == "high"


def test_turn_content_blocks_match_strands_gemini_docs() -> None:
    png = b"\x89PNG"
    pdf = b"%PDF-1.4"
    mp4 = b"\x00\x00ftyp"
    blocks = turn_content_blocks(
        TurnInput(
            prompt="What is this?",
            images=[TurnImage(format="png", data=base64.b64encode(png).decode())],
            documents=[TurnDocument(format="pdf", data=base64.b64encode(pdf).decode())],
            videos=[TurnVideo(format="mp4", data=base64.b64encode(mp4).decode())],
        )
    )
    assert blocks == [
        {"text": "What is this?"},
        {"image": {"format": "png", "source": {"bytes": png}}},
        {"document": {"format": "pdf", "source": {"bytes": pdf}}},
        {"video": {"format": "mp4", "source": {"bytes": mp4}}},
    ]


def test_turn_content_blocks_drop_unsupported_formats() -> None:
    junk = base64.b64encode(b"nope").decode()
    blocks = turn_content_blocks(
        TurnInput(
            prompt="",
            images=[TurnImage(format="bmp", data=junk)],
            documents=[TurnDocument(format="docx", data=junk)],
            videos=[TurnVideo(format="mkv", data=junk)],
        )
    )
    assert blocks == []


def test_gemini_model_formats_documented_video_block() -> None:
    model = GeminiModel(client_args={"api_key": "test"}, model_id="gemini-3.7-flash")
    part = model._format_request_content_part(
        {"video": {"format": "mp4", "source": {"bytes": b"abc"}}},
        {},
    )
    assert part.inline_data.data == b"abc"
    assert part.inline_data.mime_type in {"video/mp4", "video/mpeg"}
    assert "mp4" in part.inline_data.mime_type


def test_gemini_model_decodes_base64_image_bytes() -> None:
    model = GeminiModel(client_args={"api_key": "test"}, model_id="gemini-3.7-flash")
    png = b"\x89PNG"
    part = model._format_request_content_part(
        {"image": {"format": "png", "source": {"bytes": base64.b64encode(png).decode()}}},
        {},
    )
    assert part.inline_data.data == png
    assert part.inline_data.mime_type == "image/png"


def test_maps_from_grounding_reads_documented_widget_token_and_places() -> None:
    class Maps:
        title = "Caffe Trieste"
        uri = "https://maps.google.com/?cid=1"
        place_id = "places/ChIJ123"

    class Chunk:
        maps = Maps()

    class Grounding:
        google_maps_widget_context_token = "tok_1"
        grounding_chunks = [Chunk()]

    class Candidate:
        grounding_metadata = Grounding()

    class Event:
        candidates = [Candidate()]

    assert maps_from_grounding(Event()) == {
        "type": "google_maps",
        "google_maps_widget_context_token": "tok_1",
        "places": [
            {
                "title": "Caffe Trieste",
                "uri": "https://maps.google.com/?cid=1",
                "placeId": "places/ChIJ123",
            }
        ],
    }


def test_maps_from_grounding_is_none_without_maps_fields() -> None:
    class Candidate:
        grounding_metadata = None

    class Event:
        candidates = [Candidate()]

    assert maps_from_grounding(Event()) is None


def test_search_from_grounding_reads_web_chunks_queries_and_images() -> None:
    class Web:
        title = "Caffe Trieste"
        uri = "https://www.caffetrieste.com/"

    class Image:
        title = "Espresso"
        image_uri = "https://example.com/espresso.jpg"
        source_uri = "https://example.com/"

    class WebChunk:
        web = Web()
        image = None
        maps = None

    class ImageChunk:
        web = None
        image = Image()
        maps = None

    class Grounding:
        web_search_queries = ["best espresso north beach"]
        grounding_chunks = [WebChunk(), ImageChunk()]

    class Candidate:
        grounding_metadata = Grounding()

    class Event:
        candidates = [Candidate()]

    assert search_from_grounding(Event()) == {
        "type": "google_search",
        "queries": ["best espresso north beach"],
        "results": [
            {"title": "Caffe Trieste", "uri": "https://www.caffetrieste.com/"}
        ],
        "images": [
            {
                "title": "Espresso",
                "image_uri": "https://example.com/espresso.jpg",
                "source_uri": "https://example.com/",
            }
        ],
    }


def test_search_from_grounding_is_none_without_search_fields() -> None:
    class Candidate:
        grounding_metadata = None

    class Event:
        candidates = [Candidate()]

    assert search_from_grounding(Event()) is None


def test_computer_use_function_response_attaches_png_to_latest_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "gemini_model.capture_page_png",
        lambda: (b"png-bytes", "https://example.com/next"),
    )
    model = GeminiModel(client_args={"api_key": "test"}, model_id="gemini-3.7-flash")
    contents = model._format_request_content(
        [
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "cu-1", "name": "navigate", "input": {}}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "cu-1",
                            "status": "success",
                            "content": [
                                {
                                    "text": json.dumps(
                                        {
                                            "action": "navigate",
                                            "url": "https://example.com",
                                            "screenshot": "should-not-be-sent",
                                        }
                                    )
                                }
                            ],
                        }
                    }
                ],
            },
        ]
    )
    part = contents[-1].parts[0]
    response = part.function_response
    assert response.name == "navigate"
    assert response.response["url"] == "https://example.com/next"
    assert "screenshot" not in response.response
    assert response.parts[0].inline_data.data == b"png-bytes"
    assert response.parts[0].inline_data.mime_type == "image/png"


def test_computer_use_function_response_unwraps_temporal_activity_envelope(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "gemini_model.capture_page_png",
        lambda: (b"png-bytes", "https://example.com/live"),
    )
    model = GeminiModel(client_args={"api_key": "test"}, model_id="gemini-3.7-flash")
    inner = json.dumps(
        {
            "action": "click",
            "url": "https://example.com",
            "devtoolsFrontendUrl": "http://localhost:9222/devtools/inspector.html?ws=abc",
        }
    )
    envelope = json.dumps({"status": "success", "content": [{"text": inner}]})
    contents = model._format_request_content(
        [
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "cu-1", "name": "click", "input": {}}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "cu-1",
                            "status": "success",
                            "content": [{"text": envelope}],
                        }
                    }
                ],
            },
        ]
    )
    response = contents[-1].parts[0].function_response
    assert response.response["url"] == "https://example.com/live"
    assert response.response["devtoolsFrontendUrl"].startswith("http://localhost:9222/")
    assert response.response["action"] == "click"


def test_format_request_tools_uses_official_computer_use_not_action_declarations() -> None:
    from google.genai import types

    model = GeminiModel(
        client_args={"api_key": "test"},
        model_id="gemini-3.7-flash",
        gemini_tools=[
            types.Tool(
                computer_use=types.ComputerUse(
                    environment=types.Environment.ENVIRONMENT_BROWSER,
                    enable_prompt_injection_detection=True,
                )
            )
        ],
    )
    tools = model._format_request_tools(
        [
            {
                "name": "click",
                "description": "click",
                "inputSchema": {"json": {"type": "object"}},
            },
            {
                "name": "load_tool",
                "description": "load",
                "inputSchema": {"json": {"type": "object"}},
            },
        ]
    )
    declared = []
    computer_use = None
    for tool in tools or []:
        for declaration in tool.function_declarations or []:
            declared.append(declaration.name)
        if tool.computer_use:
            computer_use = tool.computer_use
    assert "click" not in declared
    assert "load_tool" in declared
    assert computer_use is not None
    assert computer_use.environment == types.Environment.ENVIRONMENT_BROWSER
    assert computer_use.enable_prompt_injection_detection is True


def test_model_factory_enables_official_generate_content_computer_use() -> None:
    from google.genai import types

    from config import GEMINI_MODEL_ID
    from run_worker import build_model_factory

    model = build_model_factory("test-key")[GEMINI_MODEL_ID]()
    computer_use = next(
        tool.computer_use
        for tool in model.get_config()["gemini_tools"]
        if tool.computer_use
    )
    assert computer_use.environment == types.Environment.ENVIRONMENT_BROWSER
    assert computer_use.enable_prompt_injection_detection is True
