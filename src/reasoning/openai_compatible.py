"""Client for OpenAI-compatible hosted chat completion APIs."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx


def _endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def list_models(base_url: str, api_key: str, timeout: float = 15.0) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = httpx.get(_endpoint(base_url, "models"), headers=headers, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data.get("data", []) if isinstance(data, dict) else []


def generate(
    *, base_url: str, api_key: str, model: str, prompt: str,
    images: list[Path | str] | None = None, json_format: bool = True,
    temperature: float = 0.3, timeout: float = 180.0,
) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("Cloud API key is not configured. Set it in Settings.")
    content: Any = prompt
    if images:
        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_path in images:
            path = Path(image_path)
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(suffix, "application/octet-stream")
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        content = parts

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
    }
    if json_format:
        payload["response_format"] = {"type": "json_object"}
    response = None
    for attempt in range(3):
        try:
            response = httpx.post(
                _endpoint(base_url, "chat/completions"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    try:
                        delay = float(response.headers.get("retry-after", ""))
                    except ValueError:
                        delay = 0.0
                    time.sleep(min(max(delay, 2 ** attempt), 20))
                    continue
            response.raise_for_status()
            break
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt >= 2:
                raise
            time.sleep(2 ** attempt)
    if response is None:
        raise RuntimeError("Cloud provider did not return a response.")
    result = response.json()
    text = result["choices"][0]["message"]["content"]
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    if json_format:
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return {"raw_text": str(text)}
    return {"response": str(text)}
