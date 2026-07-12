"""Evidence-selected production configuration for the native solver."""

from __future__ import annotations

from dataclasses import dataclass


CAP_CALIBRATION_MATRIX = ((8, 32), (8, 48), (16, 48), (16, 64))
RETIME_TIMEBOX_MATRIX = (1.0, 2.0, 5.0)
RETIME_PILOT_MATRIX = (0.0, 2.0, 4.0)


@dataclass(frozen=True, slots=True)
class SolverConfig:
    """Central feature flags and evidence-selected pipeline choices."""

    constructor: bool = True
    constructor_seed: int = 20260710
    constructor_profiles: tuple[str, ...] = ("PF3",)
    constructor_time_cap: int = 16
    constructor_anchor_cap: int = 48
    constructor_return_reserve_seconds: float = 0.5
    exact_retime: bool = True
    retime_backend: str = "auto"
    retime_timebox_seconds: float = 5.0
    retime_pilot_seconds: float = 0.0
    retime_threads: int = 1
    retime_final_sweep: bool = True
    alns: bool = False
    alns_destroy_min_fraction: float = 0.02
    alns_destroy_max_fraction: float = 0.06
    alns_destroy_cap_fraction: float = 0.15


DEFAULT_CONFIG = SolverConfig()
