"""em-copilot-core."""
__version__ = "0.1.dev0"

# Roadmap §6.2/§6.3 task & job state machines are part of the public surface
# so the Job-model tools (em_copilot.jobs) and the skill's validate_task.py
# can import them without reaching into private modules.
from em_copilot.states import (  # noqa: F401  (re-exported public API)
    JOB_EXCEPTION,
    JOB_TERMINAL,
    TASK_EXCEPTION,
    TASK_TERMINAL,
    JobRecord,
    JobState,
    SimulationTask,
    TaskRun,
    TaskState,
    can_transition,
    transition,
)

