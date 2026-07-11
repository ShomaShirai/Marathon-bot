import os


def get_env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def get_app_env() -> str:
    return os.getenv("APP_ENV", "local").lower()


def is_local_env() -> bool:
    return get_app_env() == "local"


def is_tennis_bot_enabled() -> bool:
    value = get_env("IS_TENNIS_BOT", "false")
    return value.lower() in {"true", "1", "yes", "on"} if value else False
