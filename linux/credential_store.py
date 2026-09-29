"""Store BMC passwords in the desktop Secret Service, never in app settings."""

from __future__ import annotations

from typing import Any


SCHEMA_NAME = "net.local.ThermalMonitor.BMC"
SECRET_LABEL = "Thermal Monitor BMC login"
_schema_cache: Any = None


def _secret_api() -> tuple[Any, Any]:
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    global _schema_cache
    if _schema_cache is None:
        _schema_cache = Secret.Schema.new(
            SCHEMA_NAME,
            Secret.SchemaFlags.NONE,
            {
                "host": Secret.SchemaAttributeType.STRING,
                "username": Secret.SchemaAttributeType.STRING,
            },
        )
    return Secret, _schema_cache


def is_available() -> bool:
    try:
        _secret_api()
        return True
    except (ImportError, ValueError):
        return False


def get_password(host: str, username: str) -> str | None:
    Secret, schema = _secret_api()
    return Secret.password_lookup_sync(
        schema, {"host": host, "username": username}, None
    )


def store_password(host: str, username: str, password: str) -> bool:
    Secret, schema = _secret_api()
    return bool(
        Secret.password_store_sync(
            schema,
            {"host": host, "username": username},
            Secret.COLLECTION_DEFAULT,
            SECRET_LABEL,
            password,
            None,
        )
    )


def clear_password(host: str, username: str) -> bool:
    Secret, schema = _secret_api()
    return bool(
        Secret.password_clear_sync(
            schema, {"host": host, "username": username}, None
        )
    )
