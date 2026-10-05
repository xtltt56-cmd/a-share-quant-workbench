from __future__ import annotations

import multiprocessing
import threading
from types import SimpleNamespace

import pandas as pd
import pytest

from a_share_quant.contracts.data import ProviderRequestError
from a_share_quant.data.realtime import sdk_process


def _send_frame(connection, endpoint, kwargs, temporary_root):
    connection.send(("FRAME", pd.DataFrame({"代码": ["000001"], "最新价": [10.25]})))
    connection.close()


def test_sdk_private_pipe_round_trips_real_process_frame_without_network(monkeypatch):
    context = multiprocessing.get_context("spawn")
    children = []

    def process_factory(*, target, args, daemon):
        process = context.Process(target=_send_frame, args=args, daemon=daemon)
        children.append(process)
        return process

    monkeypatch.setattr(
        sdk_process.multiprocessing,
        "get_context",
        lambda name: SimpleNamespace(
            Pipe=context.Pipe,
            Process=process_factory,
        ),
    )
    frame = sdk_process.call_sdk_process(
        "stock_bid_ask_em", {"symbol": "000001"}, timeout_seconds=10, stop_event=threading.Event()
    )
    assert frame["代码"].tolist() == ["000001"]
    assert frame["最新价"].tolist() == [10.25]
    assert len(children) == 1
    assert children[0]._closed is True


@pytest.mark.parametrize("cancel", [False, True])
def test_real_sdk_process_is_terminated_on_timeout_or_stop(cancel):
    stop = threading.Event()
    before = {child.pid for child in multiprocessing.active_children()}
    timer = threading.Timer(0.1, stop.set) if cancel else None
    if timer:
        timer.start()
    try:
        with pytest.raises(ProviderRequestError if cancel else TimeoutError):
            sdk_process.call_sdk_process(
                "stock_bid_ask_em", {"symbol": "000001"}, timeout_seconds=0.2, stop_event=stop
            )
    finally:
        if timer:
            timer.cancel()
            timer.join(1)
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_model_cannot_select_arbitrary_sdk_function(monkeypatch):
    monkeypatch.setattr(
        sdk_process.multiprocessing,
        "get_context",
        lambda *a: pytest.fail("must reject before spawning"),
    )
    with pytest.raises(ProviderRequestError, match="allowlisted"):
        sdk_process.call_sdk_process(
            "download_arbitrary_file", {}, timeout_seconds=1, stop_event=threading.Event()
        )
