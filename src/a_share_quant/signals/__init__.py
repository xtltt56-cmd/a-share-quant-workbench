"""Project-owned signal contracts shared by rule and Qlib adapters."""

from .adapter import prediction_frame_to_records, prediction_frame_to_signal_frame
from .frequency import ModelFrequency, ModelFrequencyError, ensure_model_frequency
from .realtime import (
    OfficialModelSignal,
    RealtimeMonitorSignal,
    RealtimeSignalState,
    TriggerConfig,
    TriggerEngine,
)
from .rule import RuleBasedSignalProvider
from .schema import SignalProvider, SignalRecord

__all__ = [
    "RuleBasedSignalProvider",
    "SignalProvider",
    "SignalRecord",
    "ModelFrequency",
    "ModelFrequencyError",
    "OfficialModelSignal",
    "RealtimeMonitorSignal",
    "RealtimeSignalState",
    "TriggerConfig",
    "TriggerEngine",
    "ensure_model_frequency",
    "prediction_frame_to_records",
    "prediction_frame_to_signal_frame",
]
