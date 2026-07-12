"""S6 packaging placeholder kept parseable without implementing later behavior."""


class StageUnsupportedError(ValueError):
    pass


def submission_rehearsal(*_args, **_kwargs):
    raise StageUnsupportedError("stage unsupported: submission rehearsal belongs to S6")
