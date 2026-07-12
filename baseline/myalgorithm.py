def algorithm(prob_info, timelimit=60):
    """Return the checker-validated OGC-SAGE incumbent."""
    try:
        from solver.entry import solve
    except ImportError:
        from baseline.solver.entry import solve
    return solve(prob_info, timelimit)
