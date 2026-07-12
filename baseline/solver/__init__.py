"""Public contracts for the OGC-SAGE solver foundation."""

from .budget import Budget
from .construct import (
    CandidateScore,
    ConstructionResult,
    ConstructionSeed,
    ConstructorConfig,
    construct_complete,
)
from .entry import solve
from .geometry import GeometryKernel, PairRelation, PairState, ShapeInfo, TemporalMode
from .instance import Instance, NoValidPlacement, OrientationInfo, ReferenceRange, parse_instance
from .retime import RetimingConfig, RetimingResult, nonfree_components, retime
from .state import CandidateDraft, IndexedSolutionState, Placement, SolutionSnapshot

__all__ = [
    "Budget",
    "CandidateDraft",
    "CandidateScore",
    "ConstructionResult",
    "ConstructionSeed",
    "ConstructorConfig",
    "GeometryKernel",
    "Instance",
    "IndexedSolutionState",
    "NoValidPlacement",
    "OrientationInfo",
    "PairRelation",
    "PairState",
    "Placement",
    "ReferenceRange",
    "RetimingConfig",
    "RetimingResult",
    "SolutionSnapshot",
    "ShapeInfo",
    "TemporalMode",
    "construct_complete",
    "nonfree_components",
    "parse_instance",
    "retime",
    "solve",
]
