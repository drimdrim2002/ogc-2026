"""Public contracts for the OGC-SAGE solver foundation."""

from .budget import Budget
from .construct import (
    CandidateScore,
    ConstructionResult,
    ConstructionSeed,
    ConstructorConfig,
    construct_complete,
    commit_insertion_candidate,
    generate_insertion_candidates,
)
from .alns import AlnsConfig, AlnsContext, AlnsMetrics, AlnsResult, run_lns
from .entry import solve
from .geometry import GeometryKernel, PairRelation, PairState, ShapeInfo, TemporalMode
from .instance import Instance, NoValidPlacement, OrientationInfo, ReferenceRange, parse_instance
from .retime import RetimingConfig, RetimingResult, nonfree_components, retime
from .neighborhoods import NeighborhoodContext, RepairResult, heuristic_repair
from .state import CandidateDraft, IndexedSolutionState, Placement, SolutionSnapshot

__all__ = [
    "Budget",
    "AlnsConfig",
    "AlnsContext",
    "AlnsMetrics",
    "AlnsResult",
    "CandidateDraft",
    "CandidateScore",
    "ConstructionResult",
    "ConstructionSeed",
    "ConstructorConfig",
    "GeometryKernel",
    "Instance",
    "IndexedSolutionState",
    "NoValidPlacement",
    "NeighborhoodContext",
    "OrientationInfo",
    "PairRelation",
    "PairState",
    "Placement",
    "ReferenceRange",
    "RetimingConfig",
    "RetimingResult",
    "RepairResult",
    "SolutionSnapshot",
    "ShapeInfo",
    "TemporalMode",
    "construct_complete",
    "commit_insertion_candidate",
    "generate_insertion_candidates",
    "heuristic_repair",
    "nonfree_components",
    "parse_instance",
    "retime",
    "run_lns",
    "solve",
]
