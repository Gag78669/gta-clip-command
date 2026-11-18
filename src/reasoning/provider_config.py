"""Read active AI provider settings from the local database."""

from src.core.config import get_settings
from src.core.db import get_db_context
from src.core.models import SystemSetting
from src.core.db import tenant_id


def get_ai_config() -> dict[str, str]:
    config = {
        "reasoning_provider": "ollama",
        "vision_provider": "ollama",
        "reasoning_model": get_settings().reasoning_model,
        "vision_model": get_settings().vision_model,
        "cloud_base_url": "https://api.groq.com/openai/v1" if get_settings().groq_api_key else "https://api.openai.com/v1",
        "cloud_api_key": "",
        "cloud_reasoning_model": "",
        "cloud_vision_model": "",
    }
    try:
        with get_db_context() as db:
            for row in db.query(SystemSetting).filter(SystemSetting.key.like("ai_%")).all():
                config[row.key.removeprefix("ai_")] = row.value
            owner = tenant_id.get()
            if owner:
                for row in db.query(SystemSetting).filter(SystemSetting.key.like(f"{owner}::ai_%")).all():
                    config[row.key.split("::", 1)[1].removeprefix("ai_")] = row.value
    except Exception:
        pass

    if config["reasoning_provider"] == "openai_compatible" and config["cloud_reasoning_model"]:
        config["reasoning_model"] = config["cloud_reasoning_model"]
    if config["vision_provider"] == "openai_compatible" and config["cloud_vision_model"]:
        config["vision_model"] = config["cloud_vision_model"]
    if config.get("cloud_api_key", "").startswith("dpapi:"):
        from src.core.secrets import decrypt_secret
        try:
            config["cloud_api_key"] = decrypt_secret(config["cloud_api_key"])
        except Exception:
            config["cloud_api_key"] = ""
    elif config.get("cloud_api_key", "").startswith("fernet:"):
        from src.core.secrets import decrypt_secret
        try:
            config["cloud_api_key"] = decrypt_secret(config["cloud_api_key"])
        except Exception:
            config["cloud_api_key"] = ""
    if not config.get("cloud_api_key"):
        config["cloud_api_key"] = get_settings().groq_api_key
    return config
