"""Task/Job state machines and the unified SimulationTask (roadmap §6.2, §6.3).

This module is the authority for task and job lifecycle in em-copilot:

- ``TaskState`` / ``JobState`` — the two state machines from §6.3.
- ``_TASK_TRANSITIONS`` / ``_JOB_TRANSITIONS`` — the legal transition tables.
- ``can_transition`` / ``transition`` — guarded state changes. Every change
  is logged (§6.3: "每次状态变化必须写入任务日志，不能只依赖聊天上下文").
- ``JobRecord`` — the per-Job metadata §6.3 requires (job_id, session_id,
  AEDT PID, project copy, Setup, submit time, worker process, log path,
  results path).
- ``SimulationTask`` — the unified task model from §6.2 (superset of the
  SI-slice ``SITask`` in ``schemas.py``). It is solver-agnostic and covers
  the AEDT solve path (HFSS / HFSS 3D Layout / Q3D / Circuit), parameter
  sweeps, outputs, acceptance, and safety flags.
- ``TaskRun`` — a live task instance: a ``SimulationTask`` + its current
  ``TaskState`` + the ``JobRecord``s spawned for it + an append-only
  transition log. This is what the Job model tools mutate.

Namespace note (roadmap §2.2/§8.2): this is enterprise domain code in
``em_copilot``. It does **not** touch the official ``ansys.aedt.mcp``
namespace; the official MCP is used as-is as the Agent tool layer, and the
Job-model tools here wrap ``analyze_design`` / ``export_results`` /
``get_pyaedt_logs``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from em_copilot.schemas import AcceptanceRule

# --------------------------------------------------------------------------- #
# State machines (roadmap §6.3)
# --------------------------------------------------------------------------- #

class TaskState(str, Enum):
    """Task lifecycle (§6.3). Happy path first, exceptions after."""

    # Happy path
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    NEEDS_INPUT = "NEEDS_INPUT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    POST_PROCESSING = "POST_PROCESSING"
    COMPLETED = "COMPLETED"
    # Exception states
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"


class JobState(str, Enum):
    """Job sub-state machine (§6.3). A task in RUNNING spawns one or more jobs."""

    # Happy path
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    SOLVING = "SOLVING"
    EXPORTING = "EXPORTING"
    SUCCEEDED = "SUCCEEDED"
    # Exception sub-states
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    ORPHANED = "ORPHANED"


# Legal task transitions. A key's value is the set of states it may move to
# in one step. Exception states are terminal unless explicitly allowed (e.g.
# NEEDS_INPUT can go back to VALIDATING after the user supplies the data).
_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.DRAFT: frozenset({TaskState.VALIDATING, TaskState.REJECTED, TaskState.CANCELLED}),
    TaskState.VALIDATING: frozenset({
        TaskState.NEEDS_INPUT, TaskState.READY_FOR_REVIEW, TaskState.REJECTED, TaskState.FAILED,
    }),
    TaskState.NEEDS_INPUT: frozenset(
        {TaskState.VALIDATING, TaskState.REJECTED, TaskState.CANCELLED}
    ),
    TaskState.READY_FOR_REVIEW: frozenset({
        TaskState.APPROVED, TaskState.NEEDS_INPUT, TaskState.REJECTED, TaskState.CANCELLED,
    }),
    TaskState.APPROVED: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset({
        TaskState.POST_PROCESSING, TaskState.FAILED, TaskState.PARTIAL_SUCCESS,
        TaskState.CANCELLED,
    }),
    TaskState.POST_PROCESSING: frozenset({
        TaskState.COMPLETED, TaskState.PARTIAL_SUCCESS, TaskState.FAILED,
    }),
    # Terminal states (no outgoing transitions by default)
    TaskState.COMPLETED: frozenset(),
    TaskState.REJECTED: frozenset(),
    TaskState.FAILED: frozenset({TaskState.RUNNING}),  # allow retry from FAILED
    TaskState.CANCELLED: frozenset({TaskState.APPROVED}),  # allow un-cancel back to approved
    TaskState.PARTIAL_SUCCESS: frozenset({TaskState.POST_PROCESSING, TaskState.COMPLETED}),
}

# Legal job transitions.
_JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.STARTING, JobState.CANCELLED, JobState.FAILED}),
    JobState.STARTING: frozenset({
        JobState.SOLVING, JobState.FAILED, JobState.CANCEL_REQUESTED,
    }),
    JobState.SOLVING: frozenset({
        JobState.EXPORTING, JobState.SUCCEEDED, JobState.FAILED,
        JobState.TIMED_OUT, JobState.CANCEL_REQUESTED,
    }),
    JobState.EXPORTING: frozenset({
        JobState.SUCCEEDED, JobState.FAILED, JobState.TIMED_OUT,
    }),
    JobState.SUCCEEDED: frozenset(),
    JobState.TIMED_OUT: frozenset({JobState.QUEUED, JobState.CANCELLED}),  # retry or give up
    JobState.FAILED: frozenset({JobState.QUEUED, JobState.CANCELLED}),  # retry or give up
    JobState.CANCEL_REQUESTED: frozenset({JobState.CANCELLED, JobState.FAILED}),
    JobState.CANCELLED: frozenset(),
    JobState.ORPHANED: frozenset({JobState.QUEUED, JobState.CANCELLED, JobState.FAILED}),
}

# States that are terminal (no productive next state). Used by callers to
# decide whether to keep polling.
TASK_TERMINAL: frozenset[TaskState] = frozenset({
    TaskState.COMPLETED, TaskState.REJECTED, TaskState.CANCELLED,
})
JOB_TERMINAL: frozenset[JobState] = frozenset({
    JobState.SUCCEEDED, JobState.CANCELLED,
})
# Exception states (the task/job is in trouble, not necessarily terminal).
TASK_EXCEPTION: frozenset[TaskState] = frozenset({
    TaskState.REJECTED, TaskState.FAILED, TaskState.CANCELLED, TaskState.PARTIAL_SUCCESS,
})
JOB_EXCEPTION: frozenset[JobState] = frozenset({
    JobState.TIMED_OUT, JobState.FAILED, JobState.CANCEL_REQUESTED,
    JobState.CANCELLED, JobState.ORPHANED,
})


def can_transition(current: TaskState | JobState, target: TaskState | JobState) -> bool:
    """True iff ``current → target`` is a legal one-step transition."""
    if isinstance(current, TaskState) and isinstance(target, TaskState):
        return target in _TASK_TRANSITIONS.get(current, frozenset())
    if isinstance(current, JobState) and isinstance(target, JobState):
        return target in _JOB_TRANSITIONS.get(current, frozenset())
    raise TypeError(f"can_transition: mismatched enum kinds: {current!r} -> {target!r}")


def transition(current: TaskState | JobState, target: TaskState | JobState) -> TaskState | JobState:
    """Return ``target`` if the transition is legal, else raise ValueError.

    Callers must record the transition in the task log (§6.3). This function
    only validates the graph edge; it does not mutate any record.
    """
    if not can_transition(current, target):
        raise ValueError(f"illegal state transition: {current.value} -> {target.value}")
    return target


# --------------------------------------------------------------------------- #
# Unified SimulationTask (roadmap §6.2)
# --------------------------------------------------------------------------- #

class TaskPortPair(BaseModel):
    """One port pair in the unified task (§6.2 ``ports.pairs``).

    ``positive``/``negative`` are 1-based single-ended port indices (strings
    in the YAML, coerced to int here). ``references`` lists the reference
    conductor port(s)/net(s) for the pair — required for AEDT solve tasks
    (see ``boundaries-and-ports.md``); may be empty for pure-SI tasks where
    the reference is implicit in the Touchstone.
    """

    name: str = Field(..., description="Pair name, e.g. 'TX0'")
    positive: int = Field(..., ge=1, description="Positive port (1-based)")
    negative: int = Field(..., ge=1, description="Negative port (1-based)")
    references: list[str] = Field(
        default_factory=list,
        description="Reference conductor ports/nets for this pair",
    )

    @model_validator(mode="after")
    def _check_distinct(self) -> "TaskPortPair":
        if self.positive == self.negative:
            raise ValueError(f"pair {self.name!r}: positive and negative must differ")
        return self


class TaskInput(BaseModel):
    """§6.2 ``input`` block."""

    project: str = Field(..., description="Path to the input .aedt/.aedtz (read-only)")
    design: str = Field("", description="Design name within the project")
    copy_before_run: bool = Field(
        True,
        description="Always copy the input to a run dir; never mutate the input (§14.1)",
    )


class TaskSolver(BaseModel):
    """§6.2 ``solver`` block. Frequencies carry units (parsed at use time)."""

    application: str = Field(..., description="hfss | hfss3d_layout | q3d | circuit")
    setup: str = Field("Setup1")
    sweep: str = Field("Sweep1")
    start_frequency: str = Field("0Hz", description="e.g. '10MHz' — include 0 Hz if TDR required")
    stop_frequency: str = Field(..., description="e.g. '30GHz'")
    cores: int = Field(0, ge=0, description="Solver cores; 0 = default")

    @field_validator("application")
    @classmethod
    def _check_application(cls, v: str) -> str:
        valid = {"hfss", "hfss3d_layout", "q3d", "circuit"}
        if v not in valid:
            raise ValueError(f"solver.application '{v}' not in {sorted(valid)}")
        return v


class TaskPorts(BaseModel):
    """§6.2 ``ports`` block."""

    mode: str = Field("differential", description="differential | single_ended")
    pairs: list[TaskPortPair] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        valid = {"differential", "single_ended"}
        if v not in valid:
            raise ValueError(f"ports.mode '{v}' not in {sorted(valid)}")
        return v

    @model_validator(mode="after")
    def _check_unique_ports(self) -> "TaskPorts":
        seen: set[int] = set()
        for p in self.pairs:
            for port in (p.positive, p.negative):
                if port in seen:
                    raise ValueError(f"port {port} appears in more than one pair")
                seen.add(port)
        return self


class TaskOutputs(BaseModel):
    """§6.2 ``outputs`` block."""

    touchstone: bool = Field(True)
    expressions: list[str] = Field(
        default_factory=list,
        description="Report expressions, e.g. 'dB(Sdd21)' (free-form, for reporting)",
    )
    report_format: list[str] = Field(
        default_factory=lambda: ["markdown"],
        description="markdown | html | json (metrics.json always written, §13.3)",
    )


class TaskSafety(BaseModel):
    """§6.2 ``safety`` block + roadmap §14 file-safety defaults."""

    overwrite_source: bool = Field(
        False,
        description="MUST be False; inputs are read-only (§14.1). Writes go to the run dir copy.",
    )
    require_port_confirmation: bool = Field(
        True, description="Ports must be confirmed before solve"
    )
    require_preflight_report: bool = Field(
        True, description="Preflight report + approval before solve"
    )
    require_explicit_solve_confirmation: bool = Field(
        True, description="L3 gate: solves need explicit user confirmation (§14.2)"
    )

    @model_validator(mode="after")
    def _no_overwrite_source(self) -> "TaskSafety":
        if self.overwrite_source:
            raise ValueError(
                "safety.overwrite_source must be False; inputs are read-only (roadmap §14.1)"
            )
        return self


class SimulationTask(BaseModel):
    """Unified task model (roadmap §6.2).

    This is the solver-agnostic task surface the Agent drafts before any
    solve. It is a superset of the SI-slice ``SITask`` in ``schemas.py``:
    ``SITask`` covers the HFSS-license-free Touchstone post-processing path;
    ``SimulationTask`` covers the full AEDT solve path (ports with references,
    solver selection, parameter sweeps, safety flags).

    The ``acceptance`` rules reuse ``AcceptanceRule`` from ``schemas.py`` so
    the same ``evaluate_acceptance`` path works for both the SI slice and the
    unified task. Note: ``AcceptanceRule.expression`` is the *bare metric
    name* (``Sdd21``), not the report expression (``dB(Sdd21)``); the latter
    lives in ``outputs.expressions``.
    """

    schema_version: str = Field("1.0")
    task_id: str = Field(..., description="Stable unique id, e.g. 'connector-20260902-001'")
    task_type: str = Field(
        ..., description="inspection | touchstone_si | pcb_channel | connector_sweep"
    )
    input: TaskInput
    solver: TaskSolver
    ports: TaskPorts = Field(default_factory=TaskPorts)
    parameters: dict[str, list[float | str]] = Field(
        default_factory=dict,
        description="Parametric sweep variables, e.g. {'plastic_dk': [3.2, 3.4, 3.6]}",
    )
    outputs: TaskOutputs = Field(default_factory=TaskOutputs)
    acceptance: list[AcceptanceRule] = Field(default_factory=list)
    safety: TaskSafety = Field(default_factory=TaskSafety)

    @field_validator("task_type")
    @classmethod
    def _check_task_type(cls, v: str) -> str:
        valid = {"inspection", "touchstone_si", "pcb_channel", "connector_sweep"}
        if v not in valid:
            raise ValueError(f"task_type '{v}' not in {sorted(valid)}")
        return v

    @model_validator(mode="after")
    def _touchstone_task_needs_no_solver(self) -> "SimulationTask":
        """A pure touchstone_si task has no AEDT solve; relax solver checks.

        For touchstone_si the solver block is still required structurally (so
        the same model serializes cleanly), but callers should treat the
        solver as informational only and route to the em_copilot SI pipeline.
        """
        return self


# --------------------------------------------------------------------------- #
# Job record + live task run (roadmap §6.3 last paragraph)
# --------------------------------------------------------------------------- #

class JobRecord(BaseModel):
    """Per-Job metadata (§6.3: "Job记录至少包含 job_id、session_id、AEDT PID、
    工程副本、Setup、提交时间、Worker进程、日志路径和结果路径")."""

    job_id: str
    session_id: str = Field("", description="em_copilot session id (App handle registry key)")
    aedt_pid: int | None = Field(None, description="AEDT process PID; None until started")
    project_copy: str = Field("", description="Path to the copied project in the run dir")
    setup: str = Field("", description="Setup name being solved")
    submit_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    worker_process: str = Field("", description="Worker process identifier, if any")
    log_path: str = Field("", description="Path to the job's incremental log")
    results_path: str = Field("", description="Path to collected results (Touchstone/etc.)")
    state: JobState = Field(JobState.QUEUED, description="Current job sub-state")
    # Transition log: list of (from, to, timestamp, note) tuples.
    history: list[dict[str, Any]] = Field(default_factory=list)

    def advance(self, target: JobState, note: str = "") -> JobState:
        """Advance the job to ``target``, validating the transition.

        Appends a history entry (§6.3 transition logging). Raises ValueError
        on an illegal transition.
        """
        prev = self.state
        transition(prev, target)  # raises if illegal
        self.state = target
        self.history.append({
            "from": prev.value,
            "to": target.value,
            "at": datetime.now(timezone.utc).isoformat(),
            "note": note,
        })
        return target


class TaskRun(BaseModel):
    """A live task instance: the SimulationTask + its state + jobs + log.

    The Job-model tools (``submit_analysis`` etc., to be implemented in
    ``em_copilot.jobs``) operate on this object. State changes go through
    ``advance()`` so the transition is validated and logged.
    """

    task: SimulationTask
    state: TaskState = Field(TaskState.DRAFT)
    run_dir: str = Field(
        "", description="Task run directory; all outputs go here, not the input dir"
    )
    input_hash: str = Field("", description="sha256[:16] of the input project (§14.1)")
    jobs: list[JobRecord] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)

    def advance(self, target: TaskState, note: str = "") -> TaskState:
        """Advance the task to ``target``, validating + logging the transition."""
        prev = self.state
        transition(prev, target)  # raises if illegal
        self.state = target
        self.history.append({
            "from": prev.value,
            "to": target.value,
            "at": datetime.now(timezone.utc).isoformat(),
            "note": note,
        })
        return target

    def add_job(self, job: JobRecord) -> None:
        """Register a new Job spawned for this task (task must be RUNNING)."""
        self.jobs.append(job)

    def job(self, job_id: str) -> JobRecord | None:
        for j in self.jobs:
            if j.job_id == job_id:
                return j
        return None

    @model_validator(mode="after")
    def _run_dir_not_in_input(self) -> "TaskRun":
        """Roadmap §14.1: outputs never written back to the input dir."""
        in_path = self.task.input.project
        if in_path and self.run_dir:
            from pathlib import Path
            in_parent = str(Path(in_path).resolve().parent)
            run_resolved = str(Path(self.run_dir).resolve())
            sep = "\\" if "\\" in in_parent else "/"
            if run_resolved == in_parent or run_resolved.startswith(in_parent.rstrip("/\\") + sep):
                raise ValueError(
                    f"file-safety: run_dir {self.run_dir!r} is inside the input project dir "
                    f"{in_parent!r}; outputs must go elsewhere (§14.1)"
                )
        return self


__all__ = [
    "TaskState",
    "JobState",
    "TASK_TERMINAL",
    "JOB_TERMINAL",
    "TASK_EXCEPTION",
    "JOB_EXCEPTION",
    "can_transition",
    "transition",
    "TaskPortPair",
    "TaskInput",
    "TaskSolver",
    "TaskPorts",
    "TaskOutputs",
    "TaskSafety",
    "SimulationTask",
    "JobRecord",
    "TaskRun",
]
