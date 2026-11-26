"""Read active AI provider settings from the local database."""

from src.core.config import get_settings
from src.core.db import get_db_context
from src.core.models import SystemSetting


def get_ai_config() -> dict[str, str]:
    config = {
        "reasoning_provider": "ollama",
        "vision_provider": "ollama",
        "transcription_provider": "faster_whisper",
        "reasoning_model": get_settings().reasoning_model,
        "vision_model": get_settings().vision_model,
        "cloud_base_url": "https://api.openai.com/v1",
        "cloud_api_key": "",
        "cloud_reasoning_model": "",
        "cloud_vision_model": "",
        "cloud_transcription_model": "whisper-large-v3-turbo",
    }
    try:
        with get_db_context() as db:
            for row in db.query(SystemSetting).filter(SystemSetting.key.like("ai_%")).all():
                config[row.key.removeprefix("ai_")] = row.value
    except Exception:
        pass

    if config["reasoning_provider"] == "openai_compatible" and config["cloud_reasoning_model"]:
        config["reasoning_model"] = config["cloud_reasoning_model"]
    if config["vision_provider"] == "openai_compatible" and config["cloud_vision_model"]:
        config["vision_model"] = config["cloud_vision_model"]
    if config.get("cloud_api_key"):
        from src.core.secrets import decrypt_secret
        try:
            config["cloud_api_key"] = decrypt_secret(config["cloud_api_key"])
        except Exception:
            config["cloud_api_key"] = ""
    return config
