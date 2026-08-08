from pathlib import Path

from a_share_quant.config import Stage2Config


def test_stage2_config_uses_next_trading_day_pit_policy() -> None:
    config = Stage2Config.load(config_dir=Path("config"))

    assert config.pit.announcement_day_policy == "next_trading_day"
    assert config.pit.allow_same_day_announcement is False
    assert config.pit.unknown_announcement_date == "reject"


def test_stage2_config_freezes_one_label_and_shared_comparison_settings() -> None:
    config = Stage2Config.load(config_dir=Path("config"))

    assert config.label.name == "forward_excess_return_5d"
    assert config.label.horizon_days == 5
    assert config.comparison.benchmark == "000300"
    assert config.comparison.seed == 42
    assert config.comparison.max_positions == 10
    assert config.comparison.max_single_position == 0.15
    assert config.comparison.split.train_ratio == 0.6
    assert config.comparison.split.validation_ratio == 0.2
    assert config.comparison.split.test_ratio == 0.2
