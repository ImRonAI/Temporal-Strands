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
from strands.types.exceptions import ContextWindowOverflowException, ModelThrottledException
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

from computer_use_activity import COMPUTER_USE_TOOL_NAMES, capture_page_png


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


class GeminiModel(_GeminiModel):
    def _format_request_content(self, messages: Messages) -> list[genai.types.Content]:
        names: dict[str, str] = {}
        last_cu: str | None = None
        for message in messages:
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if "toolUse" in block:
                    names[block["toolUse"]["toolUseId"]] = block["toolUse"]["name"]
                result = block.get("toolResult")
                if result:
                    tool_use_id = result["toolUseId"]
                    if names.get(tool_use_id) in COMPUTER_USE_TOOL_NAMES:
                        last_cu = tool_use_id
        self._latest_cu_tool_result_id = last_cu
        try:
            return super()._format_request_content(messages)
        finally:
            self._latest_cu_tool_result_id = None

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
        png = None
        if tool_use_id == getattr(self, "_latest_cu_tool_result_id", None):
            png, page_url = capture_page_png()
            if page_url:
                payload["url"] = page_url
        response = payload or {"url": ""}
        parts = (
            [genai.types.FunctionResponsePart.from_bytes(data=png, mime_type="image/png")]
            if png
            else None
        )
        return genai.types.Part(
            function_response=genai.types.FunctionResponse(
                id=tool_use_id,
                name=function_name,
                response=response,
                parts=parts,
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
        filtered = [
            spec
            for spec in (tool_specs or [])
            if spec.get("name") not in COMPUTER_USE_TOOL_NAMES
        ]
        return super()._format_request_tools(filtered or None)

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        # Identical to strands.models.gemini.GeminiModel.stream except we keep
        # GroundingMetadata across chunks. generateContent docs put maps sources
        # on candidates[0].grounding_metadata.grounding_chunks[].maps
        # (title, uri, placeId) — the last stream event is often usage-only.
        # https://ai.google.dev/gemini-api/docs/generate-content/maps-grounding
        request = self._format_request(messages, tool_specs, system_prompt, self.config.get("params"))
        client = self._get_client().aio
        try:
            response = await client.models.generate_content_stream(**request)
            yield self._format_chunk({"chunk_type": "message_start"})
            data_type: str | None = None
            tool_used = False
            candidate = None
            event = None
            maps = None
            search = None
            async for event in response:
                found_maps = maps_from_grounding(event)
                if found_maps:
                    maps = found_maps
                found_search = search_from_grounding(event)
                if found_search:
                    search = found_search
                candidates = event.candidates
                candidate = candidates[0] if candidates else None
                content = candidate.content if candidate else None
                parts = content.parts if content and content.parts else []
                # Thought tokens must stream before tool-use frames so the SSE
                # bridge can emit reasoning-delta before tool-input-start closes
                # the open reasoning block during native Computer Use loops.
                for part in parts:
                    if part.text:
                        new_data_type = "reasoning_content" if part.thought else "text"
                        if new_data_type != data_type:
                            if data_type is not None:
                                yield self._format_chunk({"chunk_type": "content_stop", "data_type": data_type})
                            yield self._format_chunk({"chunk_type": "content_start", "data_type": new_data_type})
                            data_type = new_data_type
                        yield self._format_chunk(
                            {
                                "chunk_type": "content_delta",
                                "data_type": data_type,
                                "data": part,
                            },
                        )
                    if part.function_call:
                        if data_type is not None:
                            yield self._format_chunk({"chunk_type": "content_stop", "data_type": data_type})
                            data_type = None
                        yield self._format_chunk({"chunk_type": "content_start", "data_type": "tool", "data": part})
                        yield self._format_chunk({"chunk_type": "content_delta", "data_type": "tool", "data": part})
                        yield self._format_chunk({"chunk_type": "content_stop", "data_type": "tool", "data": part})
                        tool_used = True
            if data_type is not None:
                yield self._format_chunk({"chunk_type": "content_stop", "data_type": data_type})
            yield self._format_chunk(
                {
                    "chunk_type": "message_stop",
                    "data": "TOOL_USE" if tool_used else (candidate.finish_reason if candidate else "STOP"),
                }
            )
            if search:
                yield {"gemini": search}  # type: ignore[typeddict-item]
            if maps:
                yield {"gemini": maps}  # type: ignore[typeddict-item]
            if event:
                yield self._format_chunk({"chunk_type": "metadata", "data": event.usage_metadata})
        except genai.errors.ClientError as error:
            match error.status:
                case "RESOURCE_EXHAUSTED" | "UNAVAILABLE":
                    raise ModelThrottledException(error.message or str(error)) from error
                case "INVALID_ARGUMENT":
                    if error.message and "exceeds the maximum number of tokens" in error.message:
                        raise ContextWindowOverflowException(error.message) from error
                    raise error
                case _:
                    raise error


__all__ = ["GeminiModel", "maps_from_grounding", "search_from_grounding"]
