"""Submission entry delegating to the checker-protected native solver."""


def algorithm(prob_info, timelimit=60):
    """Return only a solution stored by the verified-incumbent protocol."""
    try:
        from solver.entry import solve
    except ModuleNotFoundError as exc:
        if exc.name != "solver":
            raise
        from baseline.solver.entry import solve

    return solve(prob_info, timelimit)
