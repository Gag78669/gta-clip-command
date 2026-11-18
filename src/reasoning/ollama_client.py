"""Hardware-aware Ollama REST API client with strict single-model VRAM eviction."""

import base64
import json
from pathlib import Path
import httpx
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("reasoning.ollama")
settings = get_settings()


class OllamaClient:
    """Communicates with the local Ollama instance and enforces GPU VRAM clearance."""

    def __init__(self, base_url: str | None = None, timeout_sec: float = 120.0):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.timeout = timeout_sec

    def is_alive(self) -> bool:
        """Verify Ollama server connectivity."""
        try:
            with httpx.Client(timeout=5.0) as client:
                res = client.get(f"{self.base_url}/api/tags")
                return res.status_code == 200
        except Exception:
            return False

    def list_loaded_models(self) -> list[str]:
        """Query currently active models occupying GPU/CPU memory."""
        try:
            with httpx.Client(timeout=5.0) as client:
                res = client.get(f"{self.base_url}/api/ps")
                if res.status_code == 200:
                    models = res.json().get("models", [])
                    return [m.get("name", "") for m in models]
        except Exception as e:
            logger.warning("Failed to query loaded models: %s", e)
        return []

    def evict_model(self, model_name: str) -> bool:
        """Force Ollama to immediately unload a model from GPU VRAM by setting keep_alive to 0."""
        logger.info("Evicting model '%s' from VRAM...", model_name)
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model_name, "keep_alive": 0},
                )
                if res.status_code == 200:
                    logger.info("Successfully evicted '%s' from VRAM.", model_name)
                    return True
        except Exception as e:
            logger.warning("Model eviction request failed for '%s': %s", model_name, e)
        return False

    def evict_all(self) -> None:
        """Evict all active models to ensure 100% of GPU memory is freed."""
        loaded = self.list_loaded_models()
        for m in loaded:
            self.evict_model(m)

    @staticmethod
    def encode_image(image_path: Path | str) -> str:
        """Convert an image file to Base64 ASCII string for Ollama vision API."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def generate(
        self,
        model: str,
        prompt: str,
        images: list[Path | str] | None = None,
        json_format: bool = True,
        temperature: float = 0.3,
        auto_evict: bool = True,
        provider: str = "ollama",
    ) -> dict:
        """Execute inference with automatic post-generation VRAM eviction."""
        if provider == "openai_compatible":
            from src.reasoning.openai_compatible import generate as cloud_generate
            from src.reasoning.provider_config import get_ai_config

            cloud = get_ai_config()
            return cloud_generate(
                base_url=cloud["cloud_base_url"],
                api_key=cloud["cloud_api_key"],
                model=model,
                prompt=prompt,
                images=images,
                json_format=json_format,
                temperature=temperature,
                timeout=self.timeout,
            )

        logger.info("Invoking Ollama model '%s' (json_format=%s)...", model, json_format)

        b64_images = []
        if images:
            for img in images:
                p = Path(img)
                if p.exists():
                    b64_images.append(self.encode_image(p))
                else:
                    logger.warning("Image path not found for vision inference: %s", img)

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": 8192,
            },
        }

        # Keep alive 0 forces immediate memory release after inference completes
        if auto_evict:
            payload["keep_alive"] = 0

        if json_format:
            payload["format"] = "json"

        if b64_images:
            payload["images"] = b64_images

        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                )
                if res.status_code != 200:
                    raise RuntimeError(
                        f"Ollama returned HTTP {res.status_code}: {res.text}"
                    )

                response_data = res.json()
                raw_response = response_data.get("response", "{}")

                if json_format:
                    try:
                        return json.loads(raw_response)
                    except json.JSONDecodeError:
                        logger.warning("Ollama response was not valid JSON, returning wrapped string.")
                        return {"raw_text": raw_response}
                return {"response": raw_response}

        finally:
            if auto_evict:
                # Guarantee memory cleanup
                self.evict_model(model)
