"""Stable Markdown rendering for stage-gate evidence."""

from __future__ import annotations

from typing import Any, Mapping


def render_gate_report(gate: Mapping[str, Any]) -> str:
    lines = [
        "# S0 Foundation Gate Report",
        "",
        f"Decision: **{gate.get('decision', 'UNKNOWN')}**",
        "",
        "## Evidence",
        "",
    ]
    for item in gate.get("selected_evidence", ()):
        lines.append(f"- `{item['requirement']}`: `{item['run_dir']}`")
    failures = list(gate.get("failures", ()))
    lines.extend(["", "## Failures", ""])
    lines.extend(f"- {failure}" for failure in failures)
    if not failures:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)
