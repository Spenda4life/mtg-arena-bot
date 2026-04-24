import sys
import yaml
from pathlib import Path
from src.bot import Bot


def load_config() -> dict:
    base = Path("config/settings.yaml")
    cfg = yaml.safe_load(base.read_text()) if base.exists() else {}
    # Allow local overrides (gitignored)
    local = Path("config/settings.local.yaml")
    if local.exists():
        import copy
        local_cfg = yaml.safe_load(local.read_text()) or {}
        cfg = _deep_merge(cfg, local_cfg)
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


if __name__ == "__main__":
    config = load_config()
    bot = Bot(config)
    bot.run()
