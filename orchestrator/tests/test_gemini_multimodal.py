"""Pure turn content-block assembly and Gemini video Blob formatting."""

from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from google import genai
from strands.models.gemini import GeminiModel as _StrandsGeminiModel
from strands.types.exceptions import ModelThrottledException
from temporalio.exceptions import ApplicationError

from gemini_model import GeminiModel, maps_from_grounding, search_from_grounding
from desktop_observation import resolve_observation, store_observation
from workflow import (
    TurnDocument,
    TurnImage,
    TurnInput,
    TurnVideo,
    turn_content_blocks,
)

_MESSAGES = [{"role": "user", "content": [{"text": "hi"}]}]


def _text_part(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text, thought=False, function_call=None, thought_signature=None
    )


def _search_grounding() -> SimpleNamespace:
    web = SimpleNamespace(title="Caffe Trieste", uri="https://www.caffetrieste.com/")
    chunk = SimpleNamespace(web=web, image=None, maps=None)
    return SimpleNamespace(
        web_search_queries=["best espresso north beach"],
        grounding_chunks=[chunk],
    )


def _maps_grounding() -> SimpleNamespace:
    maps = SimpleNamespace(
        title="Caffe Trieste", uri="https://maps.google.com/?cid=1", place_id="places/ChIJ123"
    )
    chunk = SimpleNamespace(web=None, image=None, maps=maps)
    return SimpleNamespace(
        google_maps_widget_context_token="tok_1",
        web_search_queries=[],
        grounding_chunks=[chunk],
    )


def _events(grounding: SimpleNamespace | None) -> list[SimpleNamespace]:
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[_text_part("hello")]),
        finish_reason="STOP",
        grounding_metadata=grounding,
    )
    usage = SimpleNamespace(
        prompt_token_count=3, total_token_count=10, cached_content_token_count=None
    )
    return [
        SimpleNamespace(candidates=[candidate], usage_metadata=None),
        SimpleNamespace(candidates=[], usage_metadata=usage),
    ]


def _fake_client(events: list[SimpleNamespace]) -> SimpleNamespace:
    async def generate_content_stream(**kwargs):
        async def gen():
            for event in events:
                yield event

        return gen()

    return SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=generate_content_stream)
        )
    )


async def _collect(model, messages=_MESSAGES):
    return [frame async for frame in model.stream(messages)]


@pytest.mark.asyncio
async def test_stream_is_thin_wrapper_over_parent_stream(monkeypatch):
    """The subclass must delegate frame production to super().stream()."""
    sentinel = [
        {"messageStart": {"role": "assistant"}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]

    async def fake_parent_stream(self, messages, tool_specs=None, system_prompt=None, tool_choice=None, **kwargs):
        for frame in sentinel:
            yield frame

    monkeypatch.setattr(_StrandsGeminiModel, "stream", fake_parent_stream)

    async def bypass_guard(**kwargs):
        raise AssertionError("parent stream bypassed: subclass hit genai client directly")

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=bypass_guard))
    )
    model = GeminiModel(client=client, model_id="gemini-3.8-flash")
    assert await _collect(model) == sentinel


@pytest.mark.asyncio
async def test_stream_yields_parent_frames_unchanged_plus_search_grounding_frame():
    events = _events(_search_grounding())
    parent = _StrandsGeminiModel(client=_fake_client(events), model_id="gemini-3.8-flash")
    parent_frames = await _collect(parent)

    model = GeminiModel(client=_fake_client(events), model_id="gemini-3.8-flash")
    frames = await _collect(model)

    # Parent frame sequence is a strict, unchanged prefix.
    assert frames[: len(parent_frames)] == parent_frames
    assert frames[len(parent_frames) :] == [
        {
            "gemini": {
                "type": "google_search",
                "queries": ["best espresso north beach"],
                "results": [
                    {"title": "Caffe Trieste", "uri": "https://www.caffetrieste.com/"}
                ],
            }
        }
    ]


@pytest.mark.asyncio
async def test_stream_appends_maps_grounding_frame():
    events = _events(_maps_grounding())
    parent = _StrandsGeminiModel(client=_fake_client(events), model_id="gemini-3.8-flash")
    parent_frames = await _collect(parent)

    model = GeminiModel(client=_fake_client(events), model_id="gemini-3.8-flash")
    frames = await _collect(model)

    assert frames[: len(parent_frames)] == parent_frames
    assert frames[len(parent_frames) :] == [
        {
            "gemini": {
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
        }
    ]


@pytest.mark.asyncio
async def test_stream_without_grounding_yields_no_gemini_frames():
    events = _events(None)
    parent = _StrandsGeminiModel(client=_fake_client(events), model_id="gemini-3.8-flash")
    parent_frames = await _collect(parent)

    model = GeminiModel(client=_fake_client(events), model_id="gemini-3.8-flash")
    frames = await _collect(model)

    assert frames == parent_frames
    assert not any("gemini" in frame for frame in frames)


@pytest.mark.asyncio
async def test_resource_exhausted_surfaces_as_throttle_via_parent():
    error = genai.errors.ClientError(
        429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}
    )

    async def generate_content_stream(**kwargs):
        raise error

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=generate_content_stream))
    )
    model = GeminiModel(client=client, model_id="gemini-3.8-flash")
    with pytest.raises(ModelThrottledException):
        await _collect(model)


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


@pytest.fixture
def desktop_image(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_ARTIFACT_ROOT", str(tmp_path))
    (tmp_path / "runtime.json").write_text(json.dumps({
        "epoch": 7, "owner": {"namespace": "test", "workflow_id": "chat-1"}, "mode": "agent",
    }))
    ref = store_observation(
        Image.new("RGB", (16, 12), "blue"), namespace="test", workflow_id="chat-1",
        operation_id="capture-1", desktop_epoch=7,
    )
    monkeypatch.setattr("gemini_model.activity.info", lambda: SimpleNamespace(namespace="test", workflow_id="chat-1"))
    return ref, resolve_observation(ref, namespace="test", workflow_id="chat-1")


def test_computer_use_function_response_attaches_png_to_latest_result(desktop_image) -> None:
    ref, png = desktop_image
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
                                            "observation": ref.model_dump(mode="json"),
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
    assert response.response["url"] == "https://example.com"
    assert "screenshot" not in response.response
    assert response.parts[0].inline_data.data == png
    assert response.parts[0].inline_data.mime_type == "image/png"


def test_computer_use_function_response_unwraps_temporal_activity_envelope(
    desktop_image,
) -> None:
    ref, png = desktop_image
    model = GeminiModel(client_args={"api_key": "test"}, model_id="gemini-3.7-flash")
    inner = json.dumps(
        {
            "action": "click",
            "url": "https://example.com",
            "devtoolsFrontendUrl": "http://localhost:9222/devtools/inspector.html?ws=abc",
        }
    )
    envelope = json.dumps({"status": "success", "content": [{"text": inner}],
                           "observation": ref.model_dump(mode="json")})
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
    assert response.response["url"] == "https://example.com"
    assert response.response["devtoolsFrontendUrl"].startswith("http://localhost:9222/")
    assert response.response["action"] == "click"
    assert response.parts[0].inline_data.data == png


@pytest.mark.parametrize("name", ["browser", "click", "take_screenshot"])
def test_gemini_hydrates_only_latest_reference_without_mutating_messages(desktop_image, name):
    ref, png = desktop_image
    old = ref.model_copy(update={"artifact_id": "missing-old-artifact"})
    messages = []
    for call_id, observation in (("old", old), ("new", ref)):
        messages.extend([
            {"role": "assistant", "content": [{"toolUse": {"toolUseId": call_id, "name": name, "input": {}}}]},
            {"role": "user", "content": [{"toolResult": {
                "toolUseId": call_id, "status": "success", "content": [{"text": json.dumps({
                    "status": "success", "observation": observation.model_dump(mode="json"),
                })}],
            }}]},
        ])
    before = copy.deepcopy(messages)
    model = GeminiModel(client_args={"api_key": "test"}, model_id="gemini-3.7-flash")
    for _ in range(2):
        contents = model._format_request_content(messages)
        images = [part.inline_data.data for content in contents for part in content.parts or [] if part.inline_data]
        responses = [part.function_response for content in contents for part in content.parts or [] if part.function_response]
        function_images = [part.inline_data.data for response in responses for part in response.parts or []]
        assert images + function_images == [png]
        assert responses[0].parts is None
        assert messages == before


def test_gemini_rejects_corrupt_reference_instead_of_capturing_host(desktop_image, monkeypatch):
    ref, _ = desktop_image
    def corrupt(*args, **kwargs):
        raise ValueError("corrupt")

    monkeypatch.setattr("gemini_model.resolve_observation", corrupt)
    messages = [
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "cu", "name": "click", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "cu", "status": "success", "content": [
            {"text": json.dumps({"observation": ref.model_dump(mode="json")})},
        ]}}]},
    ]
    model = GeminiModel(client_args={"api_key": "test"}, model_id="gemini-3.7-flash")
    with pytest.raises(ApplicationError) as error:
        model._format_request_content(messages)
    assert error.value.non_retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["browser", "click"])
async def test_gemini_request_contains_stored_pixels_through_parent_stream(desktop_image, name):
    ref, png = desktop_image
    seen = []

    async def generate_content_stream(**kwargs):
        seen.append(kwargs)

        async def events():
            for event in _events(None):
                yield event

        return events()

    model = GeminiModel(client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
        generate_content_stream=generate_content_stream,
    ))), model_id="gemini-3.7-flash")
    messages = [
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "cu", "name": name, "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "cu", "status": "success", "content": [
            {"text": json.dumps({"observation": ref.model_dump(mode="json")})},
        ]}}]},
    ]
    before = copy.deepcopy(messages)
    assert await _collect(model, messages)
    contents = seen[0]["contents"]
    # The SDK dumps its own Content types before calling the genai client.
    images = []
    for content in contents:
        for part in content["parts"]:
            if part.get("inline_data"):
                images.append(part["inline_data"]["data"])
            for response_part in (part.get("function_response") or {}).get("parts") or []:
                images.append(response_part["inline_data"]["data"])
    assert [base64.urlsafe_b64decode(image) for image in images] == [png]
    assert messages == before


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

    factories, _catalog = build_model_factory("test-key")
    model = factories[GEMINI_MODEL_ID]()
    computer_use = next(
        tool.computer_use
        for tool in model.get_config()["gemini_tools"]
        if tool.computer_use
    )
    assert computer_use.environment == types.Environment.ENVIRONMENT_BROWSER
    assert computer_use.enable_prompt_injection_detection is True
