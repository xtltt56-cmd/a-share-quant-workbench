"""Explicit transport policy without TLS weakening or silent proxy bypasses."""

from __future__ import annotations

from dataclasses import dataclass

from a_share_quant.contracts.data import ProviderConfigurationError


@dataclass(frozen=True)
class TransportPolicy:
    """Declare proxy behavior before an adapter starts external requests.

    AKShare owns its internal HTTP call sites. The project therefore never
    monkey-patches AKShare or mutates global proxy settings. An isolated
    transport can only be requested after a diagnostic authorization and is
    fail-closed until a dedicated, reviewed adapter exists.
    """

    use_system_proxy: bool = True
    isolated_transport_authorized: bool = False

    def __post_init__(self) -> None:
        if not self.use_system_proxy and not self.isolated_transport_authorized:
            raise ProviderConfigurationError(
                "isolated transport requires an approved network diagnostic"
            )

    @property
    def is_isolated(self) -> bool:
        return not self.use_system_proxy

    def require_provider_transport(self) -> None:
        if self.is_isolated:
            raise ProviderConfigurationError(
                "isolated transport is authorized but unavailable until a dedicated "
                "reviewed AKShare transport adapter is selected"
            )
