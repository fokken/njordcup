"""Errors shared by inference, review orchestration and run control."""


class ReviewError(Exception):
    pass


class ContextBudgetExceeded(ReviewError):
    """The mandatory request cannot fit without dropping review targets."""


class RunStopped(ReviewError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason
