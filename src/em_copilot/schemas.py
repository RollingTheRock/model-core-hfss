"""Pydantic schemas for the Touchstone/SI workflow (roadmap §6.2, §10).

These models are the structured ``SimulationTask`` surface for the SI slice:
they pin down port mapping, acceptance rules, and the metrics result so the
Agent never has to guess from console text (§8.3).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ComplianceStatus(str, Enum):
    """Three-state acceptance result (I-06 / IMPROVEMENT_REPORT §7).

    - ``PASS``: at least one rule was evaluated and every rule passed.
    - ``FAIL``: at least one rule was evaluated and at least one failed.
    - ``NOT_EVALUATED``: no acceptance rules were supplied — distinct from
      ``FAIL`` so a pure-analysis run does not look like a compliance failure.
    - ``ERROR``: the workflow raised before/independent of rule evaluation.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"
    ERROR = "ERROR"

    @property
    def exit_code(self) -> int:
        """Process exit code (PASS/NOT_EVALUATED → 0, FAIL → 1, ERROR → 2)."""
        return {self.PASS: 0, self.NOT_EVALUATED: 0, self.FAIL: 1, self.ERROR: 2}[self]

    @property
    def banner(self) -> str:
        """One-line display label for reports."""
        return {
            self.PASS: "✅ PASS",
            self.FAIL: "❌ FAIL",
            self.NOT_EVALUATED: "➖ NOT EVALUATED",
            self.ERROR: "⚠️ ERROR",
        }[self]

    @property
    def html_class(self) -> str:
        """CSS class for the HTML report."""
        return {
            self.PASS: "pass",
            self.FAIL: "fail",
            self.NOT_EVALUATED: "na",
            self.ERROR: "err",
        }[self]


class DifferentialPair(BaseModel):
    """One differential pair, referenced by 1-based single-ended port index."""

    name: str = Field(..., description="Pair name, e.g. 'TX0'")
    positive: int = Field(..., ge=1, description="Positive port (1-based)")
    negative: int = Field(..., ge=1, description="Negative port (1-based)")

    @model_validator(mode="after")
    def _check_distinct(self) -> "DifferentialPair":
        if self.positive == self.negative:
            raise ValueError(f"pair {self.name!r}: positive and negative must differ")
        return self


class PortMapping(BaseModel):
    """Maps single-ended Touchstone ports to differential pairs."""

    pairs: list[DifferentialPair] = Field(..., min_length=1)
    reference_impedance: float = Field(50.0, gt=0)

    @model_validator(mode="after")
    def _check_unique_ports(self) -> "PortMapping":
        seen: set[int] = set()
        for p in self.pairs:
            for port in (p.positive, p.negative):
                if port in seen:
                    raise ValueError(f"port {port} appears in more than one pair")
                seen.add(port)
        return self


class DifferentialChannel(BaseModel):
    """A point-to-point differential channel: a named near/far pair (NEW-003).

    A real multi-channel Touchstone (e.g. an 8-port / 4-pair file) is usually
    two *channels* — Victim (near+far) and Aggressor (near+far) — not four
    independent pairs. The legacy per-pair crosstalk treated the aggressor's
    near and far ends as two separate aggressors, reading the S-matrix entry
    for the aggressor's far end as if it were a second near-end stimulus. This
    model lets the user declare the true channel topology so crosstalk is
    computed per aggressor *channel*, stimulated at its near (or far) end.
    """

    name: str = Field(..., description="Channel name, e.g. 'VICTIM' or 'AGG0'")
    near_pair: str = Field(..., description="Name of the near-end DifferentialPair")
    far_pair: str = Field(..., description="Name of the far-end DifferentialPair")


class CrosstalkCase(BaseModel):
    """A crosstalk analysis case: one victim channel + ≥1 aggressor channels
    (NEW-003).

    Each aggressor is a complete point-to-point channel (near+far), not an
    independent pair. ``stimulus_end`` selects which end of every aggressor
    channel is driven ("near" = the standard NEXT/FEXT definition).
    """

    victim_channel: str = Field(..., description="Name of the victim DifferentialChannel")
    aggressor_channels: list[str] = Field(..., min_length=1)
    stimulus_end: Literal["near", "far"] = Field("near")


class AcceptanceRule(BaseModel):
    """A single pass/fail rule evaluated against computed metrics (§6.2).

    The ``expression`` Literal enumerates every metric the SI pipeline can
    resolve from a :class:`~em_copilot.mixed_mode.SIMetrics` snapshot.
    Forward-direction terms (Sdd21, Scc21, Scd21, Sdc21) cover the
    near→far stimulus direction; the reverse terms (Sdd12, Scc12, Scd12,
    Sdc12) cover far→near so a non-reciprocal channel's reverse IL/RL can
    be checked independently (NEW-004). Sdd22/Scc22 are the far-end
    reflections. The data already lives in the snapshot; only the rule
    literal was missing.
    """

    expression: Literal[
        "Sdd11", "Sdd21", "Sdd12", "Sdd22",
        "Scc11", "Scc21", "Scc12",
        "Scd21", "Sdc21", "Scd12", "Sdc12",
        "NEXT", "FEXT", "IL_margin", "RL_margin",
    ]
    frequency: str = Field(..., description="Frequency with unit, e.g. '16GHz' or '4GHz'")
    operator: Literal[">=", "<=", ">", "<", "=="]
    value: str = Field(..., description="Value with unit, e.g. '-12dB' or '0.9'")
    description: str = Field("", description="Human-readable rule purpose")


class FrequencyPoint(BaseModel):
    """A frequency of interest for tabulated results."""

    frequency: str = Field(..., description="Frequency with unit, e.g. '4GHz'")


class SITask(BaseModel):
    """Structured SI analysis task (subset of roadmap §6.2 SimulationTask)."""

    task_id: str
    touchstone: str
    port_mapping: PortMapping
    data_rate: str | None = Field(None, description="Data rate, e.g. '10Gbps' (for info)")
    acceptance: list[AcceptanceRule] = Field(default_factory=list)
    points_of_interest: list[FrequencyPoint] = Field(default_factory=list)
    compute_tdr: bool = False
    tdr_max_time: str = Field("2ns", description="TDR time window")
    # Frequency tolerance for acceptance-rule evaluation (NEW-005). A requested
    # frequency is "in tolerance" when |actual - requested| is within
    # max(abs, rel*|requested|). The legacy 5 % default accepted a 700 MHz slip
    # at 16 GHz — far too loose for SI compliance, where a 10 MHz-grid sweep
    # makes the nearest bin essentially exact. Tightened to 1 % rel / 1 MHz abs
    # and made task-configurable so a coarse-grid file can widen it explicitly.
    frequency_tolerance_rel: float = Field(0.01, ge=0.0, le=1.0)
    frequency_tolerance_abs_hz: float = Field(1e6, ge=0.0)
    # Near/far differential-pair indices for multi-pair files (NEW-001). The
    # core ``compute_metrics`` API already required an explicit ``far_pair`` for
    # n_pairs >= 3, but the CLI/batch hardcoded ``pair_index=0`` and never
    # forwarded a far pair, so a 6/8/16-port file could not be analyzed
    # end-to-end. These 0-based indices select which pair is the channel near
    # end (its Sdd11) and which is the far end (its Sdd22 / the Sdd21 target).
    # ``near_pair_index`` defaults to 0 for legacy 2-pair tasks; ``far_pair``
    # stays None so the 2-pair unambiguous fallback in compute_metrics applies.
    near_pair_index: int = Field(0, ge=0)
    far_pair_index: int | None = Field(None, ge=0)


class FrequencySpec(BaseModel):
    """A parsed frequency value + unit, normalized to Hz."""

    hz: float

    @classmethod
    def parse(cls, text: str) -> "FrequencySpec":
        text = text.strip()
        units = [("ghz", 1e9), ("mhz", 1e6), ("khz", 1e3), ("hz", 1.0)]
        low = text.lower().replace(" ", "")
        for u, mult in units:
            if low.endswith(u):
                try:
                    return cls(hz=float(low[: -len(u)]) * mult)
                except ValueError as exc:
                    raise ValueError(f"cannot parse frequency {text!r}") from exc
        # bare number → Hz
        try:
            return cls(hz=float(low))
        except ValueError as exc:
            raise ValueError(f"cannot parse frequency {text!r}") from exc


class DecibelSpec(BaseModel):
    """A parsed dB value."""

    db: float

    @classmethod
    def parse(cls, text: str) -> "DecibelSpec":
        text = text.strip().lower().replace(" ", "")
        if text.endswith("db"):
            text = text[:-2]
        try:
            return cls(db=float(text))
        except ValueError as exc:
            raise ValueError(f"cannot parse dB value {text!r}") from exc
