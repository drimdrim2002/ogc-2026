"""Public contracts for the OGC-SAGE solver foundation."""

from .budget import Budget
from .entry import solve
from .geometry import GeometryKernel, PairRelation, PairState, ShapeInfo, TemporalMode
from .instance import Instance, NoValidPlacement, OrientationInfo, ReferenceRange, parse_instance
from .state import CandidateDraft, Placement, SolutionSnapshot

__all__ = [
    "Budget",
    "CandidateDraft",
    "GeometryKernel",
    "Instance",
    "NoValidPlacement",
    "OrientationInfo",
    "PairRelation",
    "PairState",
    "Placement",
    "ReferenceRange",
    "SolutionSnapshot",
    "ShapeInfo",
    "TemporalMode",
    "parse_instance",
    "solve",
]
