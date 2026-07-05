import os


def get_env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def get_app_env() -> str:
    return os.getenv("APP_ENV", "local").lower()


def is_local_env() -> bool:
    return get_app_env() == "local"
