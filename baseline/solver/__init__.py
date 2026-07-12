"""Public contracts for the OGC-SAGE solver foundation."""

from .budget import Budget
from .entry import solve
from .instance import Instance, NoValidPlacement, OrientationInfo, ReferenceRange, parse_instance
from .state import CandidateDraft, Placement, SolutionSnapshot

__all__ = [
    "Budget",
    "CandidateDraft",
    "Instance",
    "NoValidPlacement",
    "OrientationInfo",
    "Placement",
    "ReferenceRange",
    "SolutionSnapshot",
    "parse_instance",
    "solve",
]
