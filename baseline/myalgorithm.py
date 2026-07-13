from contextlib import redirect_stderr, redirect_stdout


class _DiscardOutput:
    """File-like sink that keeps production stdout/stderr empty."""

    encoding = "utf-8"

    def write(self, value):
        return len(value)

    def flush(self):
        return None


def algorithm(prob_info, timelimit=60):
    """Return the checker-validated OGC-SAGE incumbent."""
    with redirect_stdout(_DiscardOutput()), redirect_stderr(_DiscardOutput()):
        try:
            from solver.entry import solve
        except ImportError:
            from baseline.solver.entry import solve
        return solve(prob_info, timelimit)
