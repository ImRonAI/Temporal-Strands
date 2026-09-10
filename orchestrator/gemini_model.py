"""GeminiModel with documented multimodal blocks and Maps grounding.

``strands.models.gemini.GeminiModel`` 1.50.2 formats image and document
blocks as Gemini inline Blobs. It does not handle the documented ``video``
block (raises ``TypeError: content_type=<video>``) and it drops
``grounding_metadata`` from the stream. This subclass adds the video Blob
path and forwards Grounding with Google Maps metadata so the UI can render
the documented ``<gmp-place-contextual>`` widget.

https://ai.google.dev/gemini-api/docs/generate-content/maps-grounding
https://developers.google.com/maps/documentation/javascript/reference/places-widget#PlaceContextualElement
"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import AsyncGenerator
from typing import Any

from google import genai
from strands.models.gemini import GeminiModel as _GeminiModel
from strands.types.content import ContentBlock, Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec
from temporalio import activity
from temporalio.exceptions import ApplicationError

from desktop_observation import latest_observation, resolve_observation
from workspace_state import StateConflict


def _unwrap_activity_payload(text: str) -> dict[str, Any]:
    """Parse a tool-result text block from TemporalActivityTool or the activity."""
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        return {"output": text}
    if not isinstance(parsed, dict):
        return {"output": parsed}
    content = parsed.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            inner_text = block.get("text")
            if not isinstance(inner_text, str):
                continue
            try:
                inner = json.loads(inner_text)
            except json.JSONDecodeError:
                continue
            if isinstance(inner, dict):
                return inner
    return parsed


def _blob_bytes(data: object) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return base64.b64decode(data)
    raise TypeError("image/video source bytes must be bytes or base64")


def _grounding(event: object) -> object | None:
    candidates = getattr(event, "candidates", None) or []
    candidate = candidates[0] if candidates else None
    return getattr(candidate, "grounding_metadata", None) if candidate else None


def _images(grounding: object) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for chunk in getattr(grounding, "grounding_chunks", None) or []:
        image = getattr(chunk, "image", None)
        if image is None:
            continue
        uri = getattr(image, "image_uri", None) or ""
        if not uri:
            continue
        images.append(
            {
                "title": getattr(image, "title", None) or "",
                "image_uri": uri,
                "source_uri": getattr(image, "source_uri", None) or "",
            }
        )
    return images


def maps_from_grounding(event: object) -> dict[str, Any] | None:
    """Documented GroundingMetadata maps fields from a generateContent chunk."""
    grounding = _grounding(event)
    if grounding is None:
        return None
    token = getattr(grounding, "google_maps_widget_context_token", None)
    places: list[dict[str, str]] = []
    for chunk in getattr(grounding, "grounding_chunks", None) or []:
        maps = getattr(chunk, "maps", None)
        if maps is None:
            continue
        title = getattr(maps, "title", None) or ""
        uri = getattr(maps, "uri", None) or ""
        place_id = getattr(maps, "place_id", None) or ""
        if title or uri or place_id:
            places.append({"title": title, "uri": uri, "placeId": place_id})
    if not token and not places:
        return None
    return {
        "type": "google_maps",
        "google_maps_widget_context_token": token,
        "places": places,
    }


def search_from_grounding(event: object) -> dict[str, Any] | None:
    """Google Search grounding: web chunks, queries, and image-search URIs."""
    grounding = _grounding(event)
    if grounding is None:
        return None
    results: list[dict[str, str]] = []
    for chunk in getattr(grounding, "grounding_chunks", None) or []:
        web = getattr(chunk, "web", None)
        if web is None:
            continue
        title = getattr(web, "title", None) or ""
        uri = getattr(web, "uri", None) or ""
        if title or uri:
            results.append({"title": title, "uri": uri})
    queries = [
        q for q in (getattr(grounding, "web_search_queries", None) or []) if isinstance(q, str)
    ]
    images = _images(grounding)
    if not results and not queries and not images:
        return None
    payload: dict[str, Any] = {
        "type": "google_search",
        "queries": queries,
        "results": results,
    }
    if images:
        payload["images"] = images
    return payload


class _GroundingTapModels:
    """``client.aio.models`` proxy that taps grounding metadata off raw events."""

    def __init__(self, models: Any, sink: dict[str, Any]) -> None:
        self._models = models
        self._sink = sink

    def __getattr__(self, name: str) -> Any:
        return getattr(self._models, name)

    async def generate_content_stream(self, **kwargs: Any) -> AsyncGenerator[Any, None]:
        response = await self._models.generate_content_stream(**kwargs)

        async def tapped() -> AsyncGenerator[Any, None]:
            async for event in response:
                maps = maps_from_grounding(event)
                if maps is not None:
                    self._sink["maps"] = maps
                search = search_from_grounding(event)
                if search is not None:
                    self._sink["search"] = search
                yield event

        return tapped()


class _GroundingTapAio:
    def __init__(self, aio: Any, sink: dict[str, Any]) -> None:
        self._aio = aio
        self.models = _GroundingTapModels(aio.models, sink)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._aio, name)


class _GroundingTapClient:
    """Proxy around ``genai.Client`` that observes grounding metadata.

    The parent stream loop drops ``candidates[0].grounding_metadata``; this
    proxy records the latest maps/search grounding on ``sink`` while yielding
    every raw event unchanged, so the parent frames are byte-identical.
    """

    def __init__(self, client: genai.Client, sink: dict[str, Any]) -> None:
        self._client = client
        self.aio = _GroundingTapAio(client.aio, sink)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class GeminiModel(_GeminiModel):
    _reasoning_effort: str | None = None
    _grounding: dict[str, Any]

    def _format_request_content(self, messages: Messages) -> list[genai.types.Content]:
        contents = super()._format_request_content(messages)
        try:
            observation = latest_observation(messages)
            if observation:
                call_id, name, ref = observation
                info = activity.info()
                if not info.workflow_id:
                    raise ValueError("Desktop observation requires a workflow identity")
                png = resolve_observation(ref, namespace=info.namespace, workflow_id=info.workflow_id)
        except (OSError, ValueError, StateConflict, RuntimeError) as error:
            raise ApplicationError(
                "Desktop observation unavailable; obtain a fresh screenshot",
                type="DesktopObservationUnavailable", non_retryable=True,
            ) from error
        if observation:
            if name == "browser":
                contents.append(genai.types.Content(role="user", parts=[
                    genai.types.Part(text=f"Desktop screenshot from tool call {call_id}"),
                    genai.types.Part.from_bytes(data=png, mime_type="image/png"),
                ]))
            else:
                # Gemini's native Computer Use contract requires pixels on the
                # corresponding FunctionResponse, never captured on this host.
                for content in reversed(contents):
                    for part in content.parts or []:
                        response = part.function_response
                        if response is not None and response.id == call_id:
                            response.parts = [genai.types.FunctionResponsePart.from_bytes(
                                data=png, mime_type="image/png",
                            )]
                            return contents
                raise ValueError("Desktop observation has no matching function response")
        return contents

    def _computer_use_function_response(
        self,
        content: ContentBlock,
        function_name: str,
        tool_use_id: str,
    ) -> genai.types.Part:
        """Official generateContent FunctionResponse: url JSON + page PNG.

        https://ai.google.dev/gemini-api/docs/generate-content/computer-use
        """
        payload: dict[str, Any] = {}
        for item in content["toolResult"].get("content") or []:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not text:
                continue
            parsed = _unwrap_activity_payload(text)
            parsed.pop("screenshot", None)
            parsed.pop("mediaType", None)
            payload.update(parsed)
        response = payload or {"url": ""}
        return genai.types.Part(
            function_response=genai.types.FunctionResponse(
                id=tool_use_id,
                name=function_name,
                response=response,
            )
        )

    def _format_request_content_part(
        self, content: ContentBlock, tool_use_id_to_name: dict[str, str]
    ) -> genai.types.Part:
        if "image" in content:
            fmt = content["image"]["format"]
            mime = mimetypes.types_map.get(f".{fmt}", "application/octet-stream")
            return genai.types.Part(
                inline_data=genai.types.Blob(
                    data=_blob_bytes(content["image"]["source"]["bytes"]),
                    mime_type=mime,
                ),
            )
        if "video" in content:
            fmt = content["video"]["format"]
            mime = mimetypes.types_map.get(f".{fmt}", f"video/{fmt}")
            return genai.types.Part(
                inline_data=genai.types.Blob(
                    data=_blob_bytes(content["video"]["source"]["bytes"]),
                    mime_type=mime,
                ),
            )
        if "toolResult" in content:
            from computer_use_activity import COMPUTER_USE_TOOL_NAMES

            tool_use_id = content["toolResult"]["toolUseId"]
            function_name = tool_use_id_to_name.get(tool_use_id, tool_use_id)
            if function_name in COMPUTER_USE_TOOL_NAMES:
                return self._computer_use_function_response(
                    content, function_name, tool_use_id
                )
        return super()._format_request_content_part(content, tool_use_id_to_name)

    def _format_request_tools(self, tool_specs: list[ToolSpec] | None) -> list[genai.types.Tool | Any] | None:
        """Computer Use actions come from ``gemini_tools`` ComputerUse.

        Official generateContent sends ``Tool(computer_use=…)`` for click/type/
        navigate. Custom FunctionDeclarations are only for other tools
        (https://ai.google.dev/gemini-api/docs/generate-content/computer-use).
        """
        from computer_use_activity import COMPUTER_USE_TOOL_NAMES

        filtered = [
            spec
            for spec in (tool_specs or [])
            if spec.get("name") not in COMPUTER_USE_TOOL_NAMES
        ]
        return super()._format_request_tools(filtered or None)

    def _format_request_config(
        self,
        tool_specs: list[ToolSpec] | None,
        system_prompt: str | None,
        params: dict[str, Any] | None,
    ) -> genai.types.GenerateContentConfig:
        """Merge per-turn ``reasoning_effort`` into ``thinking_config``.

        https://ai.google.dev/gemini-api/docs/thinking#set-budget
        """
        effort = self._reasoning_effort
        if effort is not None:
            thinking = (params or {}).get("thinking_config") or {}
            if isinstance(thinking, genai.types.ThinkingConfig):
                thinking = thinking.model_dump(exclude_none=True)
            params = {
                **(params or {}),
                "thinking_config": {**thinking, "thinking_level": effort},
            }
        return super()._format_request_config(tool_specs, system_prompt, params)

    def _get_client(self) -> genai.Client:
        if not hasattr(self, "_grounding"):
            self._grounding = {}
        return _GroundingTapClient(super()._get_client(), sink=self._grounding)  # type: ignore[return-value]

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        # Delegate frame production to the parent stream; the grounding tap
        # client records maps/search GroundingMetadata that the parent loop
        # drops (candidates[0].grounding_metadata — the last stream event is
        # often usage-only), and we append it as trailing {"gemini": …} frames.
        # https://ai.google.dev/gemini-api/docs/generate-content/maps-grounding
        self._reasoning_effort = (kwargs.get("invocation_state") or {}).get("reasoning_effort")
        self._grounding = {}
        try:
            async for chunk in super().stream(
                messages, tool_specs, system_prompt, tool_choice, **kwargs
            ):
                yield chunk
            if search := self._grounding.get("search"):
                yield {"gemini": search}  # type: ignore[typeddict-item]
            if maps := self._grounding.get("maps"):
                yield {"gemini": maps}  # type: ignore[typeddict-item]
        finally:
            self._reasoning_effort = None


__all__ = ["GeminiModel", "maps_from_grounding", "search_from_grounding"]
