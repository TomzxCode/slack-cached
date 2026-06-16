"""OpenAI-compatible chat completion client.

Posts to ``{base_url}/chat/completions`` using httpx, returning a parsed
``LLMResponse``. The client is intentionally minimal: it supports a single
user message and returns the first choice's text plus usage tokens. Any
compatible endpoint (OpenAI, Ollama, vLLM, LiteLLM, OpenRouter, etc.) works.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str
    raw: dict


class LLMError(RuntimeError):
    pass


async def chat_completion(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.2,
    timeout: float = 60.0,
) -> LLMResponse:
    """Call ``POST {base_url}/chat/completions`` and return the first choice.

    *base_url* should already include the API version path (e.g.
    ``https://api.openai.com/v1``); ``/chat/completions`` is appended.
    Raises ``LLMError`` on transport, HTTP, or shape errors.
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    try:
        resp = await client.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise LLMError(f"transport error calling {url}: {exc}") from exc

    if resp.status_code >= 400:
        # Try to surface the upstream error body without dumping huge payloads.
        snippet = resp.text[:500] if resp.text else ""
        raise LLMError(f"HTTP {resp.status_code} from {url}: {snippet}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise LLMError(f"non-JSON response from {url}: {resp.text[:200]}") from exc

    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"no choices in response: {data}")
    first = choices[0]
    message = first.get("message") or {}
    text = message.get("content") or ""
    usage = data.get("usage") or {}

    return LLMResponse(
        text=text,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        model=data.get("model") or model,
        raw=data,
    )
