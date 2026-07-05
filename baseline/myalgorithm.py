# myalgorithm.py
# Submission entry point for the custom algorithm.


def _is_feasible_solution(prob_info, solution):
    """Return True only when the official checker accepts the solution."""
    try:
        from utils import check_feasibility

        result = check_feasibility(prob_info, solution)
    except Exception:
        return False
    return bool(result.get("feasible"))


def _verified_serial_fallback(prob_info):
    """Build and verify the conservative serial fallback solution."""
    import baseline_greedy

    solution = baseline_greedy._serial_fallback_solution(prob_info, verify=True)
    if not _is_feasible_solution(prob_info, solution):
        raise RuntimeError("serial fallback did not pass check_feasibility")
    return solution


def algorithm(prob_info, timelimit=60):
    """
    Return a validated feasible solution for valid official challenge instances.

    The public signature is part of the challenge contract and must stay stable.
    The delegated greedy solver may raise, time out internally, or return an
    invalid candidate. This entry point only returns candidates that pass the
    official checker; otherwise it returns the verified serial fallback.
    """
    import baseline_greedy

    try:
        candidate = baseline_greedy.greedyalgorithm(prob_info, timelimit)
    except Exception:
        return _verified_serial_fallback(prob_info)

    if _is_feasible_solution(prob_info, candidate):
        return candidate

    return _verified_serial_fallback(prob_info)
