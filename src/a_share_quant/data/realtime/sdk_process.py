"""Killable official AKShare calls using standard-library Windows spawn."""

from __future__ import annotations

import importlib
import math
import multiprocessing
import os
import threading
import time
from pathlib import Path

import pandas as pd

from a_share_quant.contracts.data import ProviderRequestError

ALLOWED_ENDPOINTS = frozenset(
    {
        "stock_zh_a_spot_em",
        "stock_zh_a_spot",
        "stock_zh_a_spot_sina",
        "stock_zh_a_spot_tx",
        "stock_bid_ask_em",
        "stock_zh_index_spot_em",
        "stock_zh_index_spot_sina",
        "stock_zh_a_hist_min_em",
        "stock_zh_a_minute",
        "stock_zh_a_hist_pre_min_em",
    }
)


def _worker(connection, endpoint: str, kwargs: dict, temporary_root: str) -> None:
    try:
        os.environ["TEMP"] = os.environ["TMP"] = temporary_root
        if endpoint not in ALLOWED_ENDPOINTS:
            raise ValueError("SDK endpoint is not allowlisted")
        frame = getattr(importlib.import_module("akshare"), endpoint)(**kwargs)
        if not isinstance(frame, pd.DataFrame) or len(frame) > 10000 or len(frame.columns) > 128:
            raise ValueError("SDK result exceeds bounded frame contract")
        if frame.memory_usage(index=True, deep=True).sum() > 16 * 1024 * 1024:
            raise ValueError("SDK result exceeds memory limit")
        # Pipe is private to this known local child. Never deserialize remote
        # pickles or accept an arbitrary callable from a model or web request.
        connection.send(("FRAME", frame))
    except Exception as exc:
        connection.send(("ERROR", type(exc).__name__))
    finally:
        connection.close()


def call_sdk_process(
    endpoint: str,
    kwargs: dict,
    *,
    timeout_seconds: float,
    stop_event: threading.Event,
) -> pd.DataFrame:
    if endpoint not in ALLOWED_ENDPOINTS:
        raise ProviderRequestError("SDK endpoint is not allowlisted")
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
        raise ProviderRequestError("SDK deadline must be finite and within 120 seconds")
    if stop_event.is_set():
        raise ProviderRequestError("SDK provider is closed")
    # Reuse the application cwd; the launcher already selects the D-drive project.
    temporary_root = Path.cwd().resolve() / ".runtime" / "temp" / "realtime-sdk"
    temporary_root.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker,
        args=(child, endpoint, kwargs, str(temporary_root)),
        daemon=True,
    )
    deadline = time.monotonic() + timeout_seconds
    try:
        process.start()
        child.close()
        while not stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("SDK request deadline exceeded")
            if parent.poll(min(0.2, remaining)):
                kind, frame = parent.recv()
                if kind != "FRAME" or not isinstance(frame, pd.DataFrame):
                    raise ProviderRequestError("SDK request failed")
                return frame
            if not process.is_alive():
                raise ProviderRequestError("SDK worker exited without result")
        raise ProviderRequestError("SDK provider was closed")
    except TimeoutError:
        raise
    except (EOFError, OSError) as exc:
        raise ProviderRequestError("SDK process unavailable") from exc
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            process.close()
