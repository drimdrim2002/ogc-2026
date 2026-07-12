"""Evidence-selected production configuration for the native solver."""

from __future__ import annotations

from dataclasses import dataclass


CAP_CALIBRATION_MATRIX = ((8, 32), (8, 48), (16, 48), (16, 64))


@dataclass(frozen=True, slots=True)
class SolverConfig:
    """Central feature flags and S1 constructor calibration choices."""

    constructor: bool = True
    constructor_seed: int = 20260710
    constructor_profiles: tuple[str, ...] = ("PF3",)
    constructor_time_cap: int = 16
    constructor_anchor_cap: int = 48
    constructor_return_reserve_seconds: float = 0.5


DEFAULT_CONFIG = SolverConfig()
