"""Client for OpenAI-compatible hosted chat completion APIs."""

from __future__ import annotations

import base64
import json
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
    response = httpx.post(
        _endpoint(base_url, "chat/completions"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
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
