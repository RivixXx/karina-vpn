from pathlib import Path

import pytest

from src.app_config import ConfigError, load_config


def write_config(tmp_path, **changes):
    values = {
        "XUI_BASE": "https://xui.example.test/",
        "XUI_USER": "fixture_user",
        "XUI_PASS": "fixture_password",
        "SUB_BASE": "https://sub.example.test/",
        "CONNECT_BASE": "https://connect.example.test/",
        "INBOUND_IDS": "2,3,4,5",
        "DEFAULT_HWID_LIMIT": "2",
    }
    values.update(changes)
    path = tmp_path / "config.env"
    path.write_text("# synthetic config\n\n" + "\n".join(
        f"{key}={value}" for key, value in values.items()
    ), encoding="utf-8")
    return path


def test_valid_config_and_normalization(tmp_path):
    config = load_config(write_config(tmp_path))
    assert config.xui_base == "https://xui.example.test"
    assert config.sub_base == "https://sub.example.test"
    assert config.connect_base == "https://connect.example.test"
    assert config.inbound_ids == (2, 3, 4, 5)
    assert config.default_hwid_limit == 2


def test_comments_empty_lines_and_quotes(tmp_path):
    config = load_config(write_config(
        tmp_path, XUI_USER='"fixture_user"', XUI_PASS="'fixture_password'",
    ))
    assert config.xui_user == "fixture_user"
    assert config.xui_pass == "fixture_password"


@pytest.mark.parametrize("key", [
    "XUI_BASE", "XUI_USER", "XUI_PASS", "SUB_BASE", "CONNECT_BASE", "INBOUND_IDS",
])
def test_missing_required_key(tmp_path, key):
    with pytest.raises(ConfigError, match=key):
        load_config(write_config(tmp_path, **{key: ""}))


@pytest.mark.parametrize("value", ["abc", "1,two", "2.5", "0,2", "-1,2"])
def test_invalid_inbound_ids(tmp_path, value):
    with pytest.raises(ConfigError, match="INBOUND_IDS"):
        load_config(write_config(tmp_path, INBOUND_IDS=value))


def test_empty_inbound_list(tmp_path):
    with pytest.raises(ConfigError, match="INBOUND_IDS"):
        load_config(write_config(tmp_path, INBOUND_IDS=","))


def test_negative_hwid_rejected(tmp_path):
    with pytest.raises(ConfigError, match="DEFAULT_HWID_LIMIT"):
        load_config(write_config(tmp_path, DEFAULT_HWID_LIMIT="-1"))


def test_zero_hwid_accepted(tmp_path):
    assert load_config(write_config(tmp_path, DEFAULT_HWID_LIMIT="0")).default_hwid_limit == 0


def test_password_is_hidden_from_repr(tmp_path):
    config = load_config(write_config(tmp_path))
    assert "fixture_password" not in repr(config)


def test_mobile_config(tmp_path):
    config = load_config(write_config(
        tmp_path, PRIMARY_INBOUND_IDS="2,3,4", MOBILE_INBOUND_ID="5",
        MOBILE_TRAFFIC_GB="50",
    ))
    assert config.primary_inbound_ids == (2, 3, 4)
    assert config.mobile_inbound_id == 5
    assert config.mobile_traffic_bytes == 53687091200


@pytest.mark.parametrize("overrides", [
    {"PRIMARY_INBOUND_IDS": "2,2,4"},
    {"PRIMARY_INBOUND_IDS": "2,3,5", "MOBILE_INBOUND_ID": "5"},
    {"MOBILE_TRAFFIC_GB": "0"},
])
def test_invalid_mobile_config(tmp_path, overrides):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, **overrides))


def test_missing_file_does_not_disclose_credentials(tmp_path):
    with pytest.raises(ConfigError) as error:
        load_config(tmp_path / "missing.env")
    assert "password" not in str(error.value).lower()
