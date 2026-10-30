"""Selected PR31 bounded type-only diagnostics; never copy exception text.

Origin: 7e691312c5494dfce0c2168084a0fac745241985 coding_agent.failure_code.
Adaptation: use actual CodingBoundaryError type, preserving current policy gates.
"""

from role_ai.coding_jobs import CheckpointWriteError, CodingJobError
from role_ai.coding_tasks import CodingBoundaryError


def failure_code(error):
    current = error
    for _ in range(5):
        if isinstance(current, PermissionError):
            return "file_permission"
        if isinstance(current, CheckpointWriteError):
            return "checkpoint_io"
        current = current.__cause__ or current.__context__
        if current is None:
            break
    if isinstance(error, CodingBoundaryError):
        return "boundary_violation"
    if isinstance(error, CodingJobError):
        return "policy_violation"
    return "internal_exception"
