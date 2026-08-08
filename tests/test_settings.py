from pathlib import Path

import pytest

from a_share_quant.config import Settings


def test_settings_load_from_env_file_without_exposing_token(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "A_SHARE_QUANT_PROVIDER=tushare\n"
        "A_SHARE_QUANT_DATA_DIR=local-data\n"
        "A_SHARE_QUANT_REQUEST_TIMEOUT_SECONDS=45\n"
        "TUSHARE_TOKEN=test-placeholder\n",
        encoding="utf-8",
    )

    settings = Settings.load(env_file=env_file)

    assert settings.provider == "tushare"
    assert settings.data_dir == Path("local-data")
    assert settings.request_timeout_seconds == 45
    assert settings.tushare_token.get_secret_value() == "test-placeholder"
    assert "test-placeholder" not in repr(settings)


def test_settings_rejects_live_execution_even_if_env_requests_it(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "A_SHARE_QUANT_EXECUTION_MODE=live\nA_SHARE_QUANT_ALLOW_LIVE=true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="paper"):
        Settings.load(env_file=env_file)
