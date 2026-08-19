from __future__ import annotations

import hashlib
from functools import lru_cache
from http.client import HTTPException
import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen as default_urlopen,
)

from .bounded_subprocess import (
    BoundedSubprocessOutputError,
    run_bounded_process,
)
from .frozen_runtime import is_frozen_runtime, module_worker_command
from .models import ANTHROPIC_MESSAGES_REASONING_EFFORTS
from .process_environment import build_child_environment
from .provider_catalog import (
    resolve_model_default_reasoning_effort,
    resolve_model_reasoning_efforts,
)


MAX_SSE_EVENT_BYTES = 32 * 1024 * 1024
MAX_ENDPOINT_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_MODEL_LIST_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_SSE_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_ISOLATED_WORKER_OUTPUT_BYTES = (MAX_SSE_RESPONSE_BYTES * 2) + (1024 * 1024)


_SENSITIVE_REDIRECT_HEADERS = frozenset({"authorization", "x-api-key"})


def _url_origin(url: str) -> tuple[str, str, int] | None:
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname
    if not scheme or not hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname.casefold(), port


def _same_origin(left: str, right: str) -> bool:
    left_origin = _url_origin(left)
    right_origin = _url_origin(right)
    return left_origin is not None and left_origin == right_origin


class _EndpointRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        redirected = super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )
        if redirected is None or _same_origin(req.full_url, newurl):
            return redirected
        for header_map in (redirected.headers, redirected.unredirected_hdrs):
            for name in tuple(header_map):
                if name.casefold() in _SENSITIVE_REDIRECT_HEADERS:
                    header_map.pop(name, None)
        return redirected


@lru_cache(maxsize=1)
def _default_endpoint_opener():
    return build_opener(_EndpointRedirectHandler())


def _open_endpoint_url(request: Request, timeout_seconds: float, urlopen) -> object:
    if urlopen is default_urlopen:
        return _default_endpoint_opener().open(request, timeout=timeout_seconds)
    return urlopen(request, timeout=timeout_seconds)


def _read_response_bytes(response: object, maximum_bytes: int) -> bytes:
    reader = getattr(response, "read", None)
    if not callable(reader):
        raise EndpointError("invalid_response")
    try:
        raw = reader(maximum_bytes + 1)
    except TypeError:
        # Keep compatibility with existing injected test doubles that expose
        # only read() while still enforcing the limit before parsing.
        raw = reader()
    if not isinstance(raw, (bytes, bytearray)):
        raise EndpointError("invalid_response")
    if len(raw) > maximum_bytes:
        raise EndpointError("invalid_response")
    return bytes(raw)


class EndpointTarget(Protocol):
    model_id: str
    scan_profile: str
    api_format: str | None
    provider_preset: str
    base_url: str | None


@dataclass(frozen=True)
class EndpointRequest:
    url: str
    body: dict[str, object]
    api_format: str = "openai_chat_completions"


@dataclass(frozen=True)
class EndpointResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    response_id: str | None = None


@dataclass(frozen=True)
class DiscoveredModel:
    model_id: str
    reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str | None = None


class EndpointError(RuntimeError):
    def __init__(
        self,
        category: str,
        status_code: int | None = None,
        *,
        diagnostics: dict[str, object] | None = None,
    ) -> None:
        self.category = category
        self.status_code = status_code
        self.diagnostics = _safe_diagnostics(diagnostics)
        detail = f" (http {status_code})" if status_code is not None else ""
        super().__init__(f"endpoint request failed: {category}{detail}")


def build_endpoint_request(target: EndpointTarget, prompt: str) -> EndpointRequest:
    if not target.base_url:
        raise EndpointError("protocol_mismatch")
    base_url = target.base_url.rstrip("/")
    if target.api_format == "openai_chat_completions":
        body: dict[str, object] = {
            "model": target.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        catalog_efforts = resolve_model_reasoning_efforts(
            model_id=target.model_id,
            base_url=target.base_url,
        )
        if catalog_efforts:
            body["thinking"] = {"type": "enabled"}
            if target.scan_profile not in {"default", "codex_default"}:
                if target.scan_profile not in catalog_efforts:
                    raise EndpointError("protocol_mismatch")
                body["reasoning_effort"] = target.scan_profile
        elif target.scan_profile not in {"default", "codex_default"}:
            if target.provider_preset == "openrouter":
                body["reasoning"] = {"effort": target.scan_profile}
            else:
                body["reasoning_effort"] = target.scan_profile
        return EndpointRequest(
            url=f"{base_url}/chat/completions",
            body=body,
            api_format="openai_chat_completions",
        )
    if target.api_format == "openai_responses":
        body = {
            "model": target.model_id,
            "input": prompt,
            "store": False,
            "stream": True,
        }
        if target.scan_profile not in {"default", "codex_default"}:
            body["reasoning"] = {"effort": target.scan_profile}
        return EndpointRequest(
            url=f"{base_url}/responses",
            body=body,
            api_format="openai_responses",
        )
    if target.api_format == "anthropic_messages":
        if target.scan_profile not in {
            "default",
            *ANTHROPIC_MESSAGES_REASONING_EFFORTS,
        }:
            raise EndpointError("protocol_mismatch")
        body = {
            "model": target.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16_384,
            "stream": True,
        }
        if target.scan_profile != "default":
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": target.scan_profile}
        return EndpointRequest(
            url=f"{base_url}/messages",
            body=body,
            api_format="anthropic_messages",
        )
    raise EndpointError("protocol_mismatch")


def execute_endpoint_request(
    request: EndpointRequest,
    api_key: str,
    *,
    timeout_seconds: float = 300,
    evaluation_id: str | None = None,
    urlopen=default_urlopen,
) -> dict[str, object]:
    encoded = json.dumps(request.body, ensure_ascii=False).encode("utf-8")
    is_streaming = request.body.get("stream") is True
    headers = _request_headers(
        request.api_format,
        api_key,
        streaming=is_streaming,
    )
    if evaluation_id:
        headers["X-Modeldial-Evaluation-ID"] = evaluation_id
    http_request = Request(
        request.url,
        data=encoded,
        headers=headers,
        method="POST",
    )
    try:
        with _open_endpoint_url(http_request, timeout_seconds, urlopen) as response:
            if is_streaming and request.api_format == "openai_chat_completions":
                payload = _read_chat_completions_sse(response)
            elif is_streaming and request.api_format == "openai_responses":
                payload = _read_responses_sse(response)
            elif is_streaming and request.api_format == "anthropic_messages":
                payload = _read_anthropic_messages_sse(response)
            else:
                payload = json.loads(
                    _read_response_bytes(
                        response,
                        MAX_ENDPOINT_RESPONSE_BYTES,
                    ).decode("utf-8")
                )
    except HTTPError as exc:
        status_code = exc.code
        diagnostics = _http_error_diagnostics(exc)
        exc.close()
        raise EndpointError(
            _http_error_category(status_code),
            status_code,
            diagnostics=diagnostics,
        ) from None
    except (TimeoutError, socket.timeout):
        raise EndpointError("timeout") from None
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise EndpointError("timeout") from None
        raise EndpointError(
            "network_error",
            diagnostics={"exception_type": type(exc.reason).__name__},
        ) from None
    except (HTTPException, ConnectionError, OSError) as exc:
        raise EndpointError(
            "network_error",
            diagnostics={"exception_type": type(exc).__name__},
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        raise EndpointError("invalid_response") from None
    if not isinstance(payload, dict):
        raise EndpointError("invalid_response")
    return payload


def parse_endpoint_response(
    api_format: str,
    payload: dict[str, object],
) -> EndpointResult:
    if api_format == "openai_chat_completions":
        text = _chat_text(payload)
        usage = _dict(payload.get("usage"))
        input_details = _dict(usage.get("prompt_tokens_details"))
        details = _dict(usage.get("completion_tokens_details"))
        return EndpointResult(
            text=text,
            input_tokens=_int(usage.get("prompt_tokens")),
            output_tokens=_int(usage.get("completion_tokens")),
            reasoning_tokens=_int(details.get("reasoning_tokens")),
            cached_input_tokens=_int(input_details.get("cached_tokens")),
            cache_write_input_tokens=_first_int(
                input_details.get("cache_write_tokens"),
                input_details.get("cache_creation_tokens"),
            ),
            response_id=_optional_str(payload.get("id")),
        )
    if api_format == "openai_responses":
        text = _responses_text(payload)
        usage = _dict(payload.get("usage"))
        input_details = _dict(usage.get("input_tokens_details"))
        details = _dict(usage.get("output_tokens_details"))
        return EndpointResult(
            text=text,
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
            reasoning_tokens=_int(details.get("reasoning_tokens")),
            cached_input_tokens=_int(input_details.get("cached_tokens")),
            cache_write_input_tokens=_first_int(
                input_details.get("cache_write_tokens"),
                input_details.get("cache_creation_tokens"),
            ),
            response_id=_optional_str(payload.get("id")),
        )
    if api_format == "anthropic_messages":
        usage = _dict(payload.get("usage"))
        input_parts = [
            _int(usage.get("input_tokens")),
            _int(usage.get("cache_creation_input_tokens")),
            _int(usage.get("cache_read_input_tokens")),
        ]
        return EndpointResult(
            text=_anthropic_text(payload),
            input_tokens=(
                sum(value for value in input_parts if value is not None)
                if any(value is not None for value in input_parts)
                else None
            ),
            output_tokens=_int(usage.get("output_tokens")),
            reasoning_tokens=None,
            cached_input_tokens=_int(usage.get("cache_read_input_tokens")),
            cache_write_input_tokens=_int(
                usage.get("cache_creation_input_tokens")
            ),
            response_id=_optional_str(payload.get("id")),
        )
    raise EndpointError("protocol_mismatch")


def run_endpoint_request(
    target: EndpointTarget,
    prompt: str,
    api_key: str,
    *,
    timeout_seconds: float = 300,
    evaluation_id: str | None = None,
    urlopen=default_urlopen,
) -> EndpointResult:
    request = build_endpoint_request(target, prompt)
    payload = execute_endpoint_request(
        request,
        api_key,
        timeout_seconds=timeout_seconds,
        evaluation_id=evaluation_id,
        urlopen=urlopen,
    )
    result = parse_endpoint_response(str(target.api_format), payload)
    if not result.text.strip():
        raise EndpointError("invalid_response")
    return result


def run_endpoint_request_isolated(
    target: EndpointTarget,
    prompt: str,
    api_key: str,
    *,
    timeout_seconds: float = 300,
    evaluation_id: str | None = None,
) -> EndpointResult:
    request = build_endpoint_request(target, prompt)
    worker_input = json.dumps(
        {
            "request": {
                "url": request.url,
                "body": request.body,
                "api_format": request.api_format,
            },
            "api_key": api_key,
            "timeout_seconds": timeout_seconds,
            "evaluation_id": evaluation_id,
        },
        ensure_ascii=False,
    )
    if is_frozen_runtime():
        configured_backend_root = os.environ.get("MODELDIAL_BACKEND_ROOT", "").strip()
        if configured_backend_root:
            child_environment = build_child_environment(
                overrides={"MODELDIAL_BACKEND_ROOT": configured_backend_root}
            )
        else:
            child_environment = build_child_environment()
    else:
        child_environment = build_child_environment()
    try:
        completed = run_bounded_process(
            module_worker_command("scanner.endpoint_client", "--execute-request"),
            input=worker_input,
            text=True,
            timeout=max(1.0, float(timeout_seconds)) + 5.0,
            env=child_environment,
            output_limit_bytes=MAX_ISOLATED_WORKER_OUTPUT_BYTES,
            runner=subprocess.run,
        )
    except BoundedSubprocessOutputError as exc:
        raise EndpointError(
            "worker_failed",
            diagnostics={
                "output_limit_bytes": exc.output_limit_bytes,
                "output_total_bytes": exc.total_output_bytes,
            },
        ) from None
    except subprocess.TimeoutExpired:
        raise EndpointError("timeout") from None
    if completed.returncode != 0:
        category = "request_interrupted" if completed.returncode < 0 else "worker_failed"
        stderr = completed.stderr.encode("utf-8", errors="replace")
        raise EndpointError(
            category,
            diagnostics={
                "worker_return_code": completed.returncode,
                "stderr_bytes": len(stderr),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            },
        )
    try:
        envelope = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        raise EndpointError("invalid_response") from None
    if not isinstance(envelope, dict):
        raise EndpointError("invalid_response")
    if envelope.get("ok") is not True:
        category = str(envelope.get("category") or "invalid_response")
        raise EndpointError(
            category,
            _int(envelope.get("status_code")),
            diagnostics=_dict(envelope.get("diagnostics")),
        )
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise EndpointError("invalid_response")
    result = parse_endpoint_response(str(target.api_format), payload)
    if not result.text.strip():
        raise EndpointError("invalid_response")
    return result


def discover_models(
    base_url: str,
    api_key: str,
    *,
    api_format: str = "openai_chat_completions",
    timeout_seconds: float = 30,
    urlopen=default_urlopen,
) -> list[str]:
    return [
        model.model_id
        for model in discover_model_catalog(
            base_url,
            api_key,
            api_format=api_format,
            timeout_seconds=timeout_seconds,
            urlopen=urlopen,
        )
    ]


def discover_model_catalog(
    base_url: str,
    api_key: str,
    *,
    api_format: str = "openai_chat_completions",
    timeout_seconds: float = 30,
    urlopen=default_urlopen,
) -> list[DiscoveredModel]:
    if api_format == "anthropic_messages":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        }
    else:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
    request = Request(
        f"{base_url.rstrip('/')}/models",
        headers=headers,
        method="GET",
    )
    try:
        with _open_endpoint_url(request, timeout_seconds, urlopen) as response:
            payload = json.loads(
                _read_response_bytes(
                    response,
                    MAX_MODEL_LIST_RESPONSE_BYTES,
                ).decode("utf-8")
            )
    except HTTPError as exc:
        status_code = exc.code
        exc.close()
        raise EndpointError(_http_error_category(status_code), status_code) from None
    except (TimeoutError, socket.timeout):
        raise EndpointError("timeout") from None
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise EndpointError("timeout") from None
        raise EndpointError(
            "network_error",
            diagnostics={"exception_type": type(exc.reason).__name__},
        ) from None
    except (HTTPException, ConnectionError, OSError) as exc:
        raise EndpointError(
            "network_error",
            diagnostics={"exception_type": type(exc).__name__},
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        raise EndpointError("invalid_response") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise EndpointError("invalid_response")
    return [
        _discovered_model(item, base_url=base_url)
        for item in payload["data"]
        if isinstance(item, dict) and item.get("id")
    ]


def _discovered_model(
    item: dict[str, object],
    *,
    base_url: str,
) -> DiscoveredModel:
    model_id = str(item["id"])
    discovered_efforts = _discovered_reasoning_efforts(item)
    return DiscoveredModel(
        model_id=model_id,
        reasoning_efforts=(
            discovered_efforts
            or resolve_model_reasoning_efforts(
                model_id=model_id,
                base_url=base_url,
            )
        ),
        default_reasoning_effort=(
            _discovered_default_reasoning_effort(item)
            or resolve_model_default_reasoning_effort(
                model_id=model_id,
                base_url=base_url,
            )
        ),
    )


def _discovered_reasoning_efforts(item: dict[str, object]) -> tuple[str, ...]:
    canonical_order = ("none", "low", "medium", "high", "xhigh", "max", "ultra")
    reasoning = item.get("reasoning")
    if isinstance(reasoning, dict):
        supported = reasoning.get("supported_efforts")
        if isinstance(supported, list):
            values = {str(value).strip().lower() for value in supported if str(value).strip()}
            return tuple(value for value in canonical_order if value in values)
    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict):
        effort = capabilities.get("effort")
        if isinstance(effort, dict):
            return tuple(
                value
                for value in canonical_order
                if isinstance(effort.get(value), dict)
                and effort[value].get("supported") is True
            )
    supported = item.get("supported_reasoning_efforts")
    if isinstance(supported, list):
        values = {str(value).strip().lower() for value in supported if str(value).strip()}
        return tuple(value for value in canonical_order if value in values)
    return ()


def _discovered_default_reasoning_effort(item: dict[str, object]) -> str | None:
    reasoning = item.get("reasoning")
    if not isinstance(reasoning, dict):
        return None
    value = reasoning.get("default_effort")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower()


def _http_error_category(status_code: int) -> str:
    if status_code in {401, 403}:
        return "authentication_failed"
    if status_code == 404:
        return "model_not_found"
    if status_code == 408:
        return "timeout"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "server_error"
    return "protocol_mismatch"


def _chat_text(payload: dict[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise EndpointError("invalid_response")
    message = _dict(_dict(choices[0]).get("message"))
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for item in content
            if isinstance(item, dict)
            for part in [item]
            if item.get("type") in {"text", "output_text"}
        )
    raise EndpointError("invalid_response")


def _responses_text(payload: dict[str, object]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text
    output = payload.get("output")
    if not isinstance(output, list):
        raise EndpointError("invalid_response")
    parts: list[str] = []
    for item in output:
        item_dict = _dict(item)
        if item_dict.get("type") != "message":
            continue
        content = item_dict.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            part_dict = _dict(part)
            if part_dict.get("type") == "output_text":
                parts.append(str(part_dict.get("text") or ""))
    text = "".join(parts)
    if not text:
        raise EndpointError("invalid_response")
    return text


def _iter_sse_events(
    response: object,
    *,
    maximum_bytes: int | None = None,
) -> Iterator[tuple[str, dict[str, object] | None]]:
    readline = getattr(response, "readline", None)
    if not callable(readline):
        raise EndpointError("invalid_response")
    total_bytes = 0
    maximum_bytes = (
        MAX_SSE_RESPONSE_BYTES if maximum_bytes is None else maximum_bytes
    )
    event_name = ""
    data_lines: list[str] = []
    data_bytes = 0

    def dispatch() -> tuple[str, dict[str, object] | None] | None:
        nonlocal event_name, data_lines, data_bytes
        if not data_lines:
            event_name = ""
            data_bytes = 0
            return None
        data = "\n".join(data_lines)
        current_event_name = event_name
        event_name = ""
        data_lines = []
        data_bytes = 0
        if data == "[DONE]":
            return "[DONE]", None
        try:
            event = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            raise EndpointError("invalid_response") from None
        if not isinstance(event, dict):
            raise EndpointError("invalid_response")
        event_type = str(event.get("type") or current_event_name).strip()
        return event_type, event

    while True:
        try:
            raw_line = readline(maximum_bytes + 1)
        except TypeError:
            # Keep compatibility with existing injected test doubles that
            # expose only readline() while enforcing the cumulative limit.
            raw_line = readline()
        if raw_line in {b"", ""}:
            break
        if isinstance(raw_line, bytes):
            total_bytes += len(raw_line)
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                raise EndpointError("invalid_response") from None
        elif isinstance(raw_line, str):
            total_bytes += len(raw_line.encode("utf-8"))
            line = raw_line
        else:
            raise EndpointError("invalid_response")
        if total_bytes > maximum_bytes:
            raise EndpointError("invalid_response")
        line = line.rstrip("\r\n")
        if not line:
            dispatched = dispatch()
            if dispatched is not None:
                yield dispatched
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_bytes += len(value.encode("utf-8"))
            if data_bytes > MAX_SSE_EVENT_BYTES:
                raise EndpointError("invalid_response")
            data_lines.append(value)

    dispatched = dispatch()
    if dispatched is not None:
        yield dispatched


def _read_chat_completions_sse(
    response: object,
    *,
    maximum_bytes: int | None = None,
) -> dict[str, object]:
    response_id: str | None = None
    text_parts: list[str] = []
    usage: dict[str, object] = {}
    completed = False

    for event_type, event in _iter_sse_events(
        response,
        maximum_bytes=maximum_bytes,
    ):
        if event is None:
            completed = True
            break
        if event_type == "error" or isinstance(event.get("error"), dict):
            raise _stream_error(event_type or "error", event)
        response_id = _optional_str(event.get("id")) or response_id
        event_usage = event.get("usage")
        if isinstance(event_usage, dict):
            usage = event_usage
        choices = event.get("choices")
        if not isinstance(choices, list):
            continue
        for item in choices:
            choice = _dict(item)
            delta = _dict(choice.get("delta"))
            content = delta.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                text_parts.extend(
                    str(part.get("text") or "")
                    for value in content
                    if isinstance(value, dict)
                    for part in [value]
                    if part.get("type") in {"text", "output_text"}
                )

    if not completed:
        raise _incomplete_sse_error()
    payload: dict[str, object] = {
        "choices": [{"message": {"content": "".join(text_parts)}}],
        "usage": usage,
    }
    if response_id is not None:
        payload["id"] = response_id
    return payload


def _read_responses_sse(
    response: object,
    *,
    maximum_bytes: int | None = None,
) -> dict[str, object]:
    for event_type, event in _iter_sse_events(
        response,
        maximum_bytes=maximum_bytes,
    ):
        if event is None:
            continue
        if event_type == "response.completed":
            completed = event.get("response")
            if not isinstance(completed, dict):
                raise EndpointError("invalid_response")
            return completed
        if event_type in {"response.failed", "error"}:
            raise _stream_error(event_type, event)
        if event_type == "response.incomplete":
            raise EndpointError(
                "invalid_response",
                diagnostics={"event_type": event_type},
            )
    raise _incomplete_sse_error()


def _read_anthropic_messages_sse(
    response: object,
    *,
    maximum_bytes: int | None = None,
) -> dict[str, object]:
    response_id: str | None = None
    text_parts: dict[int, list[str]] = {}
    usage: dict[str, object] = {}
    completed = False

    for event_type, event in _iter_sse_events(
        response,
        maximum_bytes=maximum_bytes,
    ):
        if event is None:
            completed = True
            break
        if event_type == "error" or isinstance(event.get("error"), dict):
            raise _stream_error(event_type or "error", event)
        if event_type == "message_start":
            message = _dict(event.get("message"))
            response_id = _optional_str(message.get("id")) or response_id
            usage.update(_dict(message.get("usage")))
        elif event_type == "content_block_start":
            index = _int(event.get("index"))
            content_block = _dict(event.get("content_block"))
            if index is not None and content_block.get("type") == "text":
                text_parts.setdefault(index, []).append(
                    str(content_block.get("text") or "")
                )
        elif event_type == "content_block_delta":
            index = _int(event.get("index"))
            delta = _dict(event.get("delta"))
            if index is not None and delta.get("type") == "text_delta":
                text_parts.setdefault(index, []).append(
                    str(delta.get("text") or "")
                )
        elif event_type == "message_delta":
            usage.update(_dict(event.get("usage")))
        elif event_type == "message_stop":
            completed = True
            break

    if not completed:
        raise _incomplete_sse_error()
    payload = {
        "type": "message",
        "content": [
            {"type": "text", "text": "".join(text_parts[index])}
            for index in sorted(text_parts)
        ],
        "usage": usage,
    }
    if response_id is not None:
        payload["id"] = response_id
    return payload


def _incomplete_sse_error() -> EndpointError:
    return EndpointError(
        "network_error",
        diagnostics={"exception_type": "IncompleteSSEStream"},
    )


def _stream_error(
    event_type: str,
    event: dict[str, object],
) -> EndpointError:
    response = _dict(event.get("response"))
    error = _dict(response.get("error")) or _dict(event.get("error"))
    code = str(error.get("code") or error.get("type") or "").strip()[:160]
    normalized = code.lower()
    if "rate" in normalized and "limit" in normalized:
        category = "rate_limited"
    elif any(value in normalized for value in ("auth", "api_key", "permission")):
        category = "authentication_failed"
    elif "timeout" in normalized:
        category = "timeout"
    elif any(
        value in normalized
        for value in ("invalid", "unsupported", "not_found", "model_not_found")
    ):
        category = "protocol_mismatch"
    else:
        category = "server_error"
    diagnostics: dict[str, object] = {"event_type": event_type}
    if code:
        diagnostics["error_code"] = code
    return EndpointError(category, diagnostics=diagnostics)


def _anthropic_text(payload: dict[str, object]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        raise EndpointError("invalid_response")
    text = "".join(
        str(part.get("text") or "")
        for item in content
        if isinstance(item, dict)
        for part in [item]
        if part.get("type") == "text"
    )
    if not text:
        raise EndpointError("invalid_response")
    return text


def _request_headers(
    api_format: str,
    api_key: str,
    *,
    streaming: bool = False,
) -> dict[str, str]:
    if api_format == "anthropic_messages":
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if streaming else "application/json",
        }
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": (
            "text/event-stream" if streaming else "application/json"
        ),
    }


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(*values: object) -> int | None:
    for value in values:
        parsed = _int(value)
        if parsed is not None:
            return parsed
    return None


def _safe_diagnostics(payload: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, (str, int, bool))
    }


def _http_error_diagnostics(error: HTTPError) -> dict[str, object]:
    body = b""
    try:
        body = error.read(4096)
    except (HTTPException, OSError, ValueError):
        pass
    diagnostics: dict[str, object] = {
        "exception_type": "HTTPError",
        "response_body_bytes": len(body),
        "response_body_sha256": hashlib.sha256(body).hexdigest(),
    }
    headers = error.headers
    if headers is None:
        return diagnostics
    for header in (
        "Server",
        "Via",
        "X-Powered-By",
        "X-Request-ID",
        "CF-Ray",
        "Retry-After",
    ):
        value = headers.get(header)
        if value is None:
            continue
        sanitized = " ".join(str(value).split())[:160]
        if sanitized:
            diagnostics[f"header_{header.lower().replace('-', '_')}"] = sanitized
    return diagnostics


def _isolated_worker_main() -> int:
    try:
        worker_input = json.load(sys.stdin)
        request_payload = _dict(_dict(worker_input).get("request"))
        url = request_payload.get("url")
        body = request_payload.get("body")
        api_format = request_payload.get("api_format") or "openai_chat_completions"
        api_key = _dict(worker_input).get("api_key")
        timeout_seconds = float(_dict(worker_input).get("timeout_seconds") or 300)
        evaluation_id = _optional_str(_dict(worker_input).get("evaluation_id"))
        if (
            not isinstance(url, str)
            or not isinstance(body, dict)
            or not isinstance(api_format, str)
            or not isinstance(api_key, str)
        ):
            raise EndpointError("invalid_response")
        payload = execute_endpoint_request(
            EndpointRequest(url=url, body=body, api_format=api_format),
            api_key,
            timeout_seconds=timeout_seconds,
            evaluation_id=evaluation_id,
        )
        output = {"ok": True, "payload": payload}
    except (EndpointError, TypeError, ValueError) as exc:
        error = exc if isinstance(exc, EndpointError) else EndpointError("invalid_response")
        output = {
            "ok": False,
            "category": error.category,
            "status_code": error.status_code,
            "diagnostics": error.diagnostics,
        }
    except Exception as exc:
        output = {
            "ok": False,
            "category": "worker_failed",
            "status_code": None,
            "diagnostics": {"exception_type": type(exc).__name__},
        }
    print(json.dumps(output, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--execute-request"]:
        raise SystemExit(2)
    raise SystemExit(_isolated_worker_main())
