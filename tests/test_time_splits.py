from datetime import date, timedelta

from a_share_quant.experiments.splits import fixed_time_split, rolling_walk_forward


def _dates(count: int = 20) -> list[date]:
    return [date(2025, 1, 1) + timedelta(days=index) for index in range(count)]


def test_fixed_split_is_time_ordered_and_has_no_overlap() -> None:
    split = fixed_time_split(_dates(), train_ratio=0.6, validation_ratio=0.2, test_ratio=0.2)

    assert split.train[0] < split.train[1] < split.validation[0]
    assert split.validation[1] < split.test[0] < split.test[1]
    assert split.test[1] == date(2025, 1, 20)


def test_rolling_walk_forward_only_moves_forward() -> None:
    windows = list(
        rolling_walk_forward(
            _dates(30),
            min_train_dates=10,
            validation_dates=5,
            test_dates=5,
            step_dates=5,
        )
    )

    assert len(windows) == 3
    assert windows[0].train[1] < windows[0].validation[0] < windows[0].test[0]
    assert windows[1].test[0] > windows[0].test[0]
    assert all(window.train[1] < window.validation[0] < window.test[0] for window in windows)

