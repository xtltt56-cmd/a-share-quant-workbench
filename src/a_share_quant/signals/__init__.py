"""Project-owned signal contracts shared by rule and Qlib adapters."""

from .adapter import prediction_frame_to_records, prediction_frame_to_signal_frame
from .rule import RuleBasedSignalProvider
from .schema import SignalProvider, SignalRecord

__all__ = [
    "RuleBasedSignalProvider",
    "SignalProvider",
    "SignalRecord",
    "prediction_frame_to_records",
    "prediction_frame_to_signal_frame",
]
