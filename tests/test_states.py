"""Tests for the task/job state machines and SimulationTask model (roadmap §6.2/§6.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from em_copilot import (
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

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _minimal_task(**overrides) -> SimulationTask:
    base = dict(
        task_id="t-1",
        task_type="connector_sweep",
        input={"project": "inputs/connector.aedt", "design": "HFSSDesign1"},
        solver={"application": "hfss", "stop_frequency": "30GHz"},
    )
    base.update(overrides)
    return SimulationTask(**base)


# --------------------------------------------------------------------------- #
# State machine transitions
# --------------------------------------------------------------------------- #

class TestTaskTransitions:
    def test_happy_path(self):
        path = [
            TaskState.DRAFT, TaskState.VALIDATING, TaskState.READY_FOR_REVIEW,
            TaskState.APPROVED, TaskState.RUNNING, TaskState.POST_PROCESSING,
            TaskState.COMPLETED,
        ]
        for a, b in zip(path, path[1:]):
            assert can_transition(a, b), f"{a} -> {b} should be legal"

    def test_skip_is_illegal(self):
        assert not can_transition(TaskState.DRAFT, TaskState.COMPLETED)
        assert not can_transition(TaskState.DRAFT, TaskState.RUNNING)
        assert not can_transition(TaskState.VALIDATING, TaskState.COMPLETED)

    def test_needs_input_roundtrip(self):
        # VALIDATING -> NEEDS_INPUT -> VALIDATING (user supplied the data)
        assert can_transition(TaskState.VALIDATING, TaskState.NEEDS_INPUT)
        assert can_transition(TaskState.NEEDS_INPUT, TaskState.VALIDATING)

    def test_terminal_states_have_no_outgoing(self):
        # COMPLETED and REJECTED are truly terminal: no outgoing edges at all.
        for s in (TaskState.COMPLETED, TaskState.REJECTED):
            for t in TaskState:
                assert not can_transition(s, t), f"{s} should not transition to {t}"
        # CANCELLED is terminal *for polling* but §6.3 allows un-cancel -> APPROVED
        # as its sole recovery edge; no other transition is legal from CANCELLED.
        assert can_transition(TaskState.CANCELLED, TaskState.APPROVED)
        for t in TaskState:
            if t is TaskState.APPROVED:
                continue
            assert not can_transition(TaskState.CANCELLED, t), (
                f"CANCELLED should only un-cancel to APPROVED, not reach {t}"
            )

    def test_failed_is_retriable(self):
        # FAILED -> RUNNING is allowed (retry); FAILED is an exception but not terminal
        assert can_transition(TaskState.FAILED, TaskState.RUNNING)
        assert TaskState.FAILED in TASK_EXCEPTION
        assert TaskState.FAILED not in TASK_TERMINAL

    def test_illegal_transition_raises(self):
        with pytest.raises(ValueError, match="illegal state transition"):
            transition(TaskState.DRAFT, TaskState.COMPLETED)

    def test_mismatched_enum_kinds_raise(self):
        with pytest.raises(TypeError):
            can_transition(TaskState.DRAFT, JobState.QUEUED)


class TestJobTransitions:
    def test_happy_path(self):
        path = [
            JobState.QUEUED, JobState.STARTING, JobState.SOLVING,
            JobState.EXPORTING, JobState.SUCCEEDED,
        ]
        for a, b in zip(path, path[1:]):
            assert can_transition(a, b), f"{a} -> {b} should be legal"

    def test_terminal_states(self):
        for s in JOB_TERMINAL:
            for t in JobState:
                assert not can_transition(s, t), f"{s} should not transition to {t}"

    def test_exception_states_are_in_set(self):
        for s in (
            JobState.TIMED_OUT, JobState.FAILED,
            JobState.CANCEL_REQUESTED, JobState.ORPHANED,
        ):
            assert s in JOB_EXCEPTION

    def test_timed_out_is_retriable(self):
        assert can_transition(JobState.TIMED_OUT, JobState.QUEUED)


# --------------------------------------------------------------------------- #
# TaskRun.advance logs history
# --------------------------------------------------------------------------- #

class TestTaskRunLifecycle:
    def test_advance_appends_history(self):
        run = TaskRun(task=_minimal_task(), run_dir="runs/t-1", input_hash="abc123")
        assert run.state is TaskState.DRAFT
        assert run.history == []

        run.advance(TaskState.VALIDATING, "validated")
        assert run.state is TaskState.VALIDATING
        assert len(run.history) == 1
        assert run.history[0]["from"] == "DRAFT"
        assert run.history[0]["to"] == "VALIDATING"
        assert run.history[0]["note"] == "validated"

    def test_advance_rejects_illegal(self):
        run = TaskRun(task=_minimal_task(), run_dir="runs/t-1", input_hash="abc123")
        with pytest.raises(ValueError, match="illegal state transition"):
            run.advance(TaskState.COMPLETED)

    def test_full_lifecycle_with_job(self):
        run = TaskRun(task=_minimal_task(), run_dir="runs/t-1", input_hash="abc123")
        for target, note in [
            (TaskState.VALIDATING, "validated"),
            (TaskState.READY_FOR_REVIEW, "preflight approved"),
            (TaskState.APPROVED, "L3 confirmed"),
            (TaskState.RUNNING, "submitted"),
        ]:
            run.advance(target, note)

        job = JobRecord(job_id="job-1", session_id="sess-1", setup="Setup1")
        run.add_job(job)
        assert run.job("job-1") is job
        assert run.job("missing") is None

        for target, note in [
            (JobState.STARTING, "launched"),
            (JobState.SOLVING, "solving"),
            (JobState.EXPORTING, "exporting"),
            (JobState.SUCCEEDED, "done"),
        ]:
            job.advance(target, note)

        run.advance(TaskState.POST_PROCESSING, "collected")
        run.advance(TaskState.COMPLETED, "reported")
        assert run.state is TaskState.COMPLETED
        assert job.state is JobState.SUCCEEDED
        assert len(job.history) == 4


# --------------------------------------------------------------------------- #
# SimulationTask validation
# --------------------------------------------------------------------------- #

class TestSimulationTaskValidation:
    def test_minimal_task_builds(self):
        t = _minimal_task()
        assert t.task_id == "t-1"
        assert t.solver.application == "hfss"
        assert t.safety.overwrite_source is False  # default
        assert t.safety.require_explicit_solve_confirmation is True  # L3 gate default

    def test_bad_task_type_rejected(self):
        with pytest.raises(ValidationError, match="task_type"):
            _minimal_task(task_type="magic")

    def test_bad_application_rejected(self):
        with pytest.raises(ValidationError, match="application"):
            _minimal_task(solver={"application": "maxwell", "stop_frequency": "1GHz"})

    def test_overwrite_source_rejected(self):
        with pytest.raises(ValidationError, match="overwrite_source"):
            _minimal_task(safety={"overwrite_source": True})

    def test_duplicate_port_rejected(self):
        with pytest.raises(ValidationError, match="appears in more than one pair"):
            _minimal_task(ports={
                "mode": "differential",
                "pairs": [
                    {"name": "A", "positive": 1, "negative": 2},
                    {"name": "B", "positive": 1, "negative": 3},
                ],
            })

    def test_same_pos_neg_rejected(self):
        with pytest.raises(ValidationError, match="must differ"):
            _minimal_task(ports={
                "mode": "differential",
                "pairs": [{"name": "A", "positive": 1, "negative": 1}],
            })

    def test_roadmap_example_builds(self):
        # The full example from roadmap §6.2 should build cleanly.
        t = SimulationTask(
            schema_version="1.0",
            task_id="connector-20260902-001",
            task_type="connector_sweep",
            input={
                "project": "inputs/connector_template.aedt",
                "design": "HFSSDesign1", "copy_before_run": True,
            },
            solver={
                "application": "hfss", "setup": "Setup1", "sweep": "Sweep1",
                "start_frequency": "10MHz", "stop_frequency": "30GHz",
                "cores": 8,
            },
            ports={
                "mode": "differential",
                "pairs": [
                    {
                        "name": "TX0", "positive": 1, "negative": 2,
                        "references": ["3", "4", "7", "8"],
                    },
                ],
            },
            parameters={
                "plastic_dk": [3.2, 3.4, 3.6],
                "terminal_length": ["8.0mm", "8.5mm"],
            },
            outputs={
                "touchstone": True,
                "expressions": ["dB(Sdd11)", "dB(Sdd21)", "dB(Sdc21)"],
                "report_format": ["markdown", "html"],
            },
            acceptance=[
                {
                    "expression": "Sdd21", "frequency": "16GHz",
                    "operator": ">=", "value": "-12dB",
                },
            ],
            safety={
                "overwrite_source": False,
                "require_port_confirmation": True,
                "require_preflight_report": True,
            },
        )
        assert t.ports.pairs[0].name == "TX0"
        assert t.ports.pairs[0].references == ["3", "4", "7", "8"]
        assert t.parameters["plastic_dk"] == [3.2, 3.4, 3.6]


# --------------------------------------------------------------------------- #
# File-safety: run_dir must not be inside the input dir
# --------------------------------------------------------------------------- #

class TestFileSafety:
    def test_run_dir_inside_input_rejected(self):
        t = _minimal_task(input={"project": "inputs/proj.aedt"})
        with pytest.raises(ValidationError, match="file-safety"):
            TaskRun(task=t, run_dir="inputs/sub", input_hash="abc")

    def test_run_dir_outside_input_ok(self):
        t = _minimal_task(input={"project": "inputs/proj.aedt"})
        run = TaskRun(task=t, run_dir="runs/proj-1", input_hash="abc")
        assert run.run_dir == "runs/proj-1"

    def test_run_dir_unset_ok(self):
        # No run_dir yet (preflight stage) is fine; it's set before RUNNING.
        run = TaskRun(task=_minimal_task(), input_hash="abc")
        assert run.run_dir == ""
