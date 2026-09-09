from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Configuration is missing or invalid."""


@dataclass(frozen=True)
class KarinaConfig:
    xui_base: str
    xui_user: str
    xui_pass: str = field(repr=False)
    sub_base: str
    connect_base: str
    inbound_ids: tuple[int, ...]
    default_hwid_limit: int


def load_config(path: Path | str = "/etc/karina-vpn/config.env") -> KarinaConfig:
    config_path = Path(path)
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"не найден {config_path}") from exc

    values = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value

    required = ("XUI_BASE", "XUI_USER", "XUI_PASS", "SUB_BASE", "CONNECT_BASE", "INBOUND_IDS")
    for key in required:
        if not values.get(key):
            raise ConfigError(f"в {config_path} отсутствует {key}")

    try:
        inbound_ids = tuple(int(item.strip()) for item in values["INBOUND_IDS"].split(",")
                            if item.strip())
    except ValueError as exc:
        raise ConfigError("INBOUND_IDS должен содержать целые числа") from exc
    if not inbound_ids:
        raise ConfigError("INBOUND_IDS не может быть пустым")
    if any(value <= 0 for value in inbound_ids):
        raise ConfigError("INBOUND_IDS должен содержать только положительные числа")
    try:
        default_hwid_limit = int(values.get("DEFAULT_HWID_LIMIT", "2"))
    except ValueError as exc:
        raise ConfigError("DEFAULT_HWID_LIMIT должен быть целым числом") from exc
    if default_hwid_limit < 0:
        raise ConfigError("DEFAULT_HWID_LIMIT не может быть отрицательным")

    return KarinaConfig(
        xui_base=values["XUI_BASE"].rstrip("/"),
        xui_user=values["XUI_USER"],
        xui_pass=values["XUI_PASS"],
        sub_base=values["SUB_BASE"].rstrip("/"),
        connect_base=values["CONNECT_BASE"].rstrip("/"),
        inbound_ids=inbound_ids,
        default_hwid_limit=default_hwid_limit,
    )
