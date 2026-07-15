"""Deterministic instance selectors and generated S0 fixture recipes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_ROOT = REPO_ROOT / "benchmarks" / "manifests"
TRAINING_ROOTS = (
    REPO_ROOT / "data" / "train",
    REPO_ROOT / "data" / "train 2",
    REPO_ROOT / "alg_tester" / "example" / "train",
    REPO_ROOT / "alg_tester" / "example" / "train 2",
)
SMOKE_IDS = ("prob_21", "prob_32", "prob_9")
DEV_IDS = (
    "prob_4", "prob_8", "prob_9", "prob_13", "prob_20",
    "prob_21", "prob_32", "prob_36", "prob_18", "prob_40",
)


class SelectorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InstanceRef:
    instance_id: str
    path: Path
    sha256: str
    prob_info: dict[str, Any]


def select_instances(selector: str, *, fixture_dir: Path | None = None) -> tuple[InstanceRef, ...]:
    if selector == "example":
        path = REPO_ROOT / "alg_tester" / "example" / "example_B2_b10.json"
        return (_load_ref("example", path),)
    if selector in {"training", "smoke-3", "dev-10", "high-w23"}:
        refs = _training_refs()
        if selector == "training":
            return tuple(refs[f"prob_{index}"] for index in range(1, 41))
        if selector == "high-w23":
            eligible = tuple(
                ref for ref in refs.values() if len(ref.prob_info.get("bays", ())) >= 2
            )
            count = max(1, math.ceil(0.25 * len(eligible)))
            return tuple(
                sorted(
                    eligible,
                    key=lambda ref: (
                        -_w23_ratio(ref.prob_info),
                        int(ref.instance_id.split("_")[-1]),
                    ),
                )[:count]
            )
        ids = SMOKE_IDS if selector == "smoke-3" else DEV_IDS
        return tuple(refs[instance_id] for instance_id in ids)
    if selector in {"synthetic", "stress"}:
        if fixture_dir is None:
            raise SelectorError(f"{selector} requires an evidence fixture directory")
        recipes = _synthetic_recipes() if selector == "synthetic" else _stress_recipes()
        fixture_dir.mkdir(parents=True, exist_ok=True)
        refs = []
        for instance_id, prob_info in recipes:
            path = fixture_dir / f"{instance_id}.json"
            encoded = (json.dumps(prob_info, indent=2, sort_keys=True) + "\n").encode()
            path.write_bytes(encoded)
            refs.append(
                InstanceRef(instance_id, path, hashlib.sha256(encoded).hexdigest(), prob_info)
            )
        return tuple(refs)
    raise SelectorError(f"unsupported selector: {selector}")


def select_s6_stress_instances(*, fixture_dir: Path) -> tuple[InstanceRef, ...]:
    """Materialize the preregistered S6-05 structural stress corpus."""

    fixture_dir.mkdir(parents=True, exist_ok=True)
    refs = [
        _write_generated_ref(instance_id, prob_info, fixture_dir)
        for instance_id, prob_info in _s6_stress_recipes()
    ]
    training = _training_refs()
    maximum = min(
        training.values(),
        key=lambda ref: (-len(ref.prob_info["blocks"]), int(ref.instance_id.split("_")[1])),
    )
    maximum_path = fixture_dir / "s6-max-training-n.json"
    encoded = maximum.path.read_bytes()
    maximum_path.write_bytes(encoded)
    refs.append(
        InstanceRef(
            instance_id="s6-max-training-n",
            path=maximum_path,
            sha256=hashlib.sha256(encoded).hexdigest(),
            prob_info=json.loads(encoded),
        )
    )
    return tuple(refs)


def _training_refs() -> dict[str, InstanceRef]:
    expected = _expected_training_hashes()
    refs: dict[str, InstanceRef] = {}
    for index in range(1, 41):
        instance_id = f"prob_{index}"
        candidates = [root / f"{instance_id}.json" for root in TRAINING_ROOTS]
        existing = [path for path in candidates if path.is_file()]
        if not existing:
            raise SelectorError(f"missing training input {instance_id}")
        by_hash: dict[str, list[Path]] = {}
        for path in existing:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            by_hash.setdefault(digest, []).append(path)
        if len(by_hash) != 1:
            details = ", ".join(f"{digest}:{paths}" for digest, paths in by_hash.items())
            raise SelectorError(f"ambiguous training input {instance_id}: {details}")
        digest, paths = next(iter(by_hash.items()))
        if instance_id in expected and expected[instance_id] != digest:
            raise SelectorError(
                f"training hash mismatch for {instance_id}: {digest} != {expected[instance_id]}"
            )
        refs[instance_id] = _load_ref(instance_id, paths[0], digest=digest)
    return refs


def _expected_training_hashes() -> dict[str, str]:
    path = MANIFEST_ROOT / "training.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["id"]): str(row["sha256"]) for row in payload["instances"]}


def _w23_ratio(prob_info: dict[str, Any]) -> float:
    raw = prob_info.get("weights", {})
    weights = raw if isinstance(raw, dict) else {}
    w1 = float(weights.get("w1", 1.0))
    w2 = float(weights.get("w2", 1.0))
    w3 = float(weights.get("w3", 1.0))
    return (w2 + w3) / max(w1, 1.0)


def _load_ref(instance_id: str, path: Path, *, digest: str | None = None) -> InstanceRef:
    encoded = path.read_bytes()
    return InstanceRef(
        instance_id=instance_id,
        path=path,
        sha256=digest or hashlib.sha256(encoded).hexdigest(),
        prob_info=json.loads(encoded),
    )


def _block(index: int, bay_count: int, *, size: int = 1) -> dict[str, Any]:
    return {
        "release_time": index % 3,
        "due_date": 20 + index,
        "processing_time": index % 4,
        "workload": index + 1,
        "bay_preferences": [100 - 3 * bay for bay in range(bay_count)],
        "shape": [{
            "orientation": 0,
            "layers": [[[0, 0], [size, 0], [size, size], [0, size]]],
        }],
    }


def _fixture(
    name: str,
    block_count: int,
    bay_count: int = 2,
    *,
    size: int = 1,
) -> dict[str, Any]:
    return {
        "name": name,
        "bays": [{"width": 12 + bay, "height": 12 + bay} for bay in range(bay_count)],
        "blocks": [_block(index, bay_count, size=size) for index in range(block_count)],
        "weights": {"w1": 1.0, "w2": 1.0, "w3": 1.0},
    }


def _synthetic_recipes() -> tuple[tuple[str, dict[str, Any]], ...]:
    return (("synthetic-valid", _fixture("synthetic-valid", 4)),)


def _stress_recipes() -> tuple[tuple[str, dict[str, Any]], ...]:
    return tuple(
        (f"stress-{blocks}", _fixture(f"stress-{blocks}", blocks))
        for blocks in (1, 4, 8)
    )


def _write_generated_ref(
    instance_id: str,
    prob_info: dict[str, Any],
    fixture_dir: Path,
) -> InstanceRef:
    path = fixture_dir / f"{instance_id}.json"
    encoded = (json.dumps(prob_info, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(encoded)
    return InstanceRef(
        instance_id=instance_id,
        path=path,
        sha256=hashlib.sha256(encoded).hexdigest(),
        prob_info=prob_info,
    )


def _s6_stress_recipes() -> tuple[tuple[str, dict[str, Any]], ...]:
    one_bay = _fixture("s6-one-bay", 6, bay_count=1)
    one_layer = _fixture("s6-one-layer", 8, bay_count=2)

    p_zero = _fixture("s6-p-zero", 8, bay_count=2)
    for block in p_zero["blocks"]:
        block["processing_time"] = 0

    contact = {
        "name": "s6-contact",
        "bays": [{"width": 4, "height": 2}],
        "blocks": [
            {
                "release_time": 0,
                "due_date": 2,
                "processing_time": 2,
                "workload": 1,
                "bay_preferences": [1],
                "shape": [{
                    "orientation": 0,
                    "layers": [[[0, 0], [2, 0], [2, 2], [0, 2]]],
                }],
            }
            for _ in range(2)
        ],
        "weights": {"w1": 1.0, "w2": 1.0, "w3": 1.0},
    }

    preference_fallback = {
        "name": "s6-preference-fallback",
        "bays": [{"width": 2, "height": 2}, {"width": 8, "height": 8}],
        "blocks": [{
            "release_time": 0,
            "due_date": 3,
            "processing_time": 2,
            "workload": 3,
            "bay_preferences": [100, 1],
            "shape": [{
                "orientation": 0,
                "layers": [[[0, 0], [4, 0], [4, 4], [0, 4]]],
            }],
        }],
        "weights": {"w1": 1.0, "w2": 1.0, "w3": 1.0},
    }

    dense = _fixture("s6-dense", 24, bay_count=1, size=3)
    dense["bays"] = [{"width": 12, "height": 12}]
    for index, block in enumerate(dense["blocks"]):
        block.update(
            release_time=0,
            due_date=5 + index // 8,
            processing_time=4,
            bay_preferences=[1],
        )

    cache_pressure = _fixture("s6-cache-pressure", 48, bay_count=2, size=2)
    for index, block in enumerate(cache_pressure["blocks"]):
        side = 1 + index % 3
        block["shape"] = [
            {
                "orientation": orientation,
                "layers": [
                    [[0, 0], [side, 0], [side, side], [0, side]],
                    [[0, 0], [1, 0], [1, 1], [0, 1]],
                ],
            }
            for orientation in (0, 90, 180, 270)
        ]

    return (
        ("s6-one-bay", one_bay),
        ("s6-one-layer", one_layer),
        ("s6-p-zero", p_zero),
        ("s6-contact", contact),
        ("s6-preference-fallback", preference_fallback),
        ("s6-dense", dense),
        ("s6-cache-pressure", cache_pressure),
    )
