"""Touchstone ``.sNp`` reader.

Parses a Touchstone file into a frequency array and a complex S-parameter
tensor of shape ``(n_freqs, n_ports, n_ports)`` where ``S[f, i, j]`` is the
S-parameter with port ``j`` as the incident (stimulus) port and port ``i`` as
the response port — the convention used throughout this package.

Supports:
- ``!`` comment lines and the ``#`` option line;
- magnitude/angle (MA), real/imaginary (RI), and dB/angle (DB) formats;
- frequency unit suffixes Hz/kHz/MHz/GHz (default Hz);
- reference impedance per the option line ``R`` (single value; per-port
  reference is read from ``!`` ``Reference Impedance`` comments when present).

This is a pure-Python, dependency-light parser — no HFSS license required,
CI-friendly (roadmap §10.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_FREQ_MULT: dict[str, float] = {
    "hz": 1.0,
    "khz": 1e3,
    "mhz": 1e6,
    "ghz": 1e9,
}


@dataclass
class Touchstone:
    """Parsed Touchstone data.

    Attributes
    ----------
    path : str
        Source file path (kept for audit/reporting).
    n_ports : int
        Number of ports (inferred from filename or matrix shape).
    freqs : ndarray, shape (n_freqs,)
        Frequencies in Hz.
    s : ndarray, shape (n_freqs, n_ports, n_ports), complex
        S-parameter tensor. ``s[f, i, j]`` = response i, stimulus j.
    z0 : float
        Reference impedance in ohms (single value). Sourced from the
        option line ``R`` keyword; when the file carries ``! Reference
        Impedance <value>`` comments, the single-value form overrides this
        (NEW-007). Per-port impedances differing from this value are
        captured in ``comments`` for audit.
    format : str
        Original data format: ``"MA"``, ``"RI"`` or ``"DB"``.
    freq_unit : str
        Original frequency unit string, e.g. ``"GHz"``.
    comments : list[str]
        Comment lines (for audit / preflight).
    """

    path: str
    n_ports: int
    freqs: np.ndarray
    s: np.ndarray
    z0: float
    format: str
    freq_unit: str
    comments: list[str]


def _parse_number(token: str) -> float:
    """Parse a Touchstone number token.

    Touchstone allows scientific notation and a leading ``+``/``-``. Some
    EDA tools emit values like ``1.234E-2`` or ``-Inf``/``NaN``; the latter
    are surfaced as ``nan`` so :func:`_check_invalid_values` can flag them.
    """
    t = token.strip()
    if not t:
        raise ValueError("empty numeric token")
    low = t.lower()
    if low in ("inf", "+inf", "-inf"):
        return float(t)
    if low in ("nan",):
        return float("nan")
    return float(t)


def _freq_multiplier(unit: str) -> float:
    key = unit.strip().lower()
    if key not in _FREQ_MULT:
        raise ValueError(f"unknown frequency unit: {unit!r}")
    return _FREQ_MULT[key]


# Standard Touchstone 1.0 two-port data order: S11, S21, S12, S22.
# ``reshape(2, 2)`` (C-order) yields [[S11, S21], [S12, S22]] which transposes
# S12/S21. Our package convention is ``S[f, response, stimulus]`` = [[S11, S12],
# [S21, S22]], so the off-diagonal entries must be swapped for 2-port files.
def _decode_s_matrix(
    values: np.ndarray,
    n_ports: int,
    *,
    two_port_order: str = "21_12",
) -> np.ndarray:
    """Reshape a flat S-row into an ``(n_ports, n_ports)`` complex matrix.

    Parameters
    ----------
    values : ndarray
        Flat complex vector of length ``n_ports * n_ports`` (one frequency
        point, already converted from MA/RI/DB to complex).
    n_ports : int
        Number of ports.
    two_port_order : str
        On-disk two-port data order. The Touchstone 1.0 default is
        ``"21_12"`` (i.e. ``S11 S21 S12 S22``); ``"12_21"`` keeps the matrix
        row-major order ``S11 S12 S21 S22``. Any value other than
        ``"21_12"`` falls back to a plain ``reshape`` (no swap), which is
        correct for n != 2 and for Touchstone 2.0 ``[Two-Port Data Order]
        12_21`` files.
    """
    if n_ports == 2 and two_port_order == "21_12":
        # Input order on disk: S11, S21, S12, S22.
        # Target (response, stimulus) matrix: [[S11, S12], [S21, S22]].
        return np.array(
            [[values[0], values[2]], [values[1], values[3]]],
            dtype=complex,
        )
    return values.reshape(n_ports, n_ports)


_SNP_RE = re.compile(r"\.s(\d+)p", re.IGNORECASE)

# Match ``! Reference Impedance <value> [unit]`` comments. Touchstone per-port
# reference-impedance comments come in several shapes across EDA tools:
#   "! Reference Impedance 75"
#   "! Reference Impedance 75 Ohm"
#   "! Reference Impedance : 75" / "! Port Impedance 50 50 50 50"
# The single scalar (option line R) and the per-port comment are kept separate
# here: the option line R is always honored as the single-value z0, and a
# ``! Reference Impedance`` comment overrides it only when it is itself a
# single scalar (per-port vectors are kept in comments for audit, since the
# Touchstone dataclass exposes a single z0) (NEW-007).
_REF_Z_RE = re.compile(
    r"^(?:reference\s+impedance|port\s+impedance)\s*:?\s*([0-9.+eE-]+)\s*(ohm)?\s*$",
    re.IGNORECASE,
)


def _ref_impedance_from_comments(comments: list[str]) -> float | None:
    """Extract a single scalar reference impedance from ``!`` comments.

    Returns the first parseable ``Reference Impedance`` / ``Port Impedance``
    scalar found, or ``None`` when no comment matches (NEW-007). Per-port
    vectors (more than one numeric token) are intentionally NOT consumed here:
    they describe a non-uniform termination the single-z0 dataclass cannot
    represent, so they stay in ``comments`` for audit.
    """
    for c in comments:
        m = _REF_Z_RE.match(c.strip())
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _infer_n_ports(path: Path) -> int:
    """Infer port count from the ``.sNp`` extension.

    Supports multi-digit port counts (e.g. ``.s10p``, ``.s16p``, ``.s128p``),
    not just the single-digit ``.s2p``–``.s9p`` range.
    """
    suffix = path.suffix.lower()
    m = _SNP_RE.fullmatch(suffix)
    if m is None:
        raise ValueError(
            f"cannot infer port count from extension {suffix!r}; "
            "expected .s2p/.s3p/.s4p/... (single or multi digit)"
        )
    return int(m.group(1))


def _check_invalid_values(freqs: np.ndarray, s: np.ndarray, path: str) -> list[str]:
    """Return a list of warnings for NaN/Inf in the data (roadmap §10.3)."""
    warnings: list[str] = []
    bad_f = int(np.count_nonzero(~np.isfinite(freqs)))
    bad_s = int(np.count_nonzero(~np.isfinite(s)))
    if bad_f:
        warnings.append(f"{bad_f} non-finite frequency values in {path}")
    if bad_s:
        warnings.append(f"{bad_s} non-finite S-parameter values in {path}")
    return warnings


def read_touchstone(path: str | Path) -> Touchstone:
    """Read a Touchstone file.

    Parameters
    ----------
    path : str or Path
        Path to the ``.sNp`` file. Port count is inferred from the extension.

    Returns
    -------
    Touchstone
        Parsed data.

    Raises
    ------
    ValueError
        If the file is malformed, the option line is inconsistent, the number
        of data columns does not match ``n_ports``, or duplicate frequencies
        are found.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"touchstone file not found: {path}")

    n_ports = _infer_n_ports(path)

    option_freq_unit = "ghz"
    option_format = "MA"
    option_z0 = 50.0
    option_line_seen = False
    comments: list[str] = []

    # Flat token accumulation: Touchstone lets a single logical data row wrap
    # across any number of physical lines, and only the FIRST physical line of
    # a row carries the frequency — continuation lines are pure data. Parsing
    # line-by-line and guessing which line "starts" a row is fragile (the
    # first data token of a continuation line gets mis-read as a frequency and
    # row boundaries desync), so we accumulate every numeric token (skipping
    # ``!``/``#`` lines) into one flat stream, then re-segment into rows of
    # ``1 freq + 2*n^2`` values. Robust to arbitrary wrapping and interleaved
    # comments; standard approach used by scikit-rf etc.
    row_size = 1 + 2 * n_ports * n_ports  # freq + 2*n^2 real numbers per row
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    flat: list[float] = []
    for lineno, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        if not line:
            continue
        # A leading ``!`` marks a full-line comment.
        if line.startswith("!"):
            comments.append(line[1:].strip())
            continue
        # Touchstone also permits inline ``!`` comments after data tokens; strip
        # everything from the first ``!`` onward on the same physical line and
        # capture the trailing comment text for audit.
        if "!" in line:
            data_part, _, cmt_part = line.partition("!")
            inline_cmt = cmt_part.strip()
            if inline_cmt:
                comments.append(inline_cmt)
            line = data_part.strip()
            if not line:
                continue
        if line.startswith("#"):
            # option line, e.g. "# Hz S MA R 50"
            option_line_seen = True
            tokens = line[1:].split()
            idx = 0
            while idx < len(tokens):
                tok = tokens[idx].lower()
                if tok in _FREQ_MULT:
                    option_freq_unit = tok
                elif tok in ("s", "y", "z"):
                    if tok != "s":
                        raise ValueError(
                            f"line {lineno}: only S-parameters supported, got {tok!r}"
                        )
                elif tok in ("ma", "ri", "db"):
                    option_format = tok.upper()
                elif tok == "r":
                    if idx + 1 >= len(tokens):
                        raise ValueError(f"line {lineno}: 'R' without value")
                    option_z0 = float(tokens[idx + 1])
                    idx += 1
                # ignore unknown tokens (e.g. 'w' wait) per Touchstone spec
                idx += 1
            continue
        for tok in line.split():
            try:
                flat.append(_parse_number(tok))
            except ValueError as exc:
                raise ValueError(
                    f"line {lineno}: cannot parse token {tok!r}: {exc}"
                ) from exc

    if not flat:
        raise ValueError(f"no data rows found in {path}")
    if len(flat) % row_size != 0:
        raise ValueError(
            f"file {path}: total numeric token count {len(flat)} is not a "
            f"multiple of {row_size} (1 freq + 2*{n_ports}^2 values) — "
            "file may be truncated or malformed"
        )
    n_freqs = len(flat) // row_size
    freq_list = [flat[i * row_size] for i in range(n_freqs)]
    data_rows = [
        flat[i * row_size + 1 : (i + 1) * row_size] for i in range(n_freqs)
    ]

    if not freq_list:
        raise ValueError(f"no data rows found in {path}")

    n_freqs = len(freq_list)
    freqs = np.asarray(freq_list, dtype=float) * _freq_multiplier(option_freq_unit)

    # Normalize each row to exactly 2*n^2 numbers.
    s = np.empty((n_freqs, n_ports, n_ports), dtype=complex)
    for i, row in enumerate(data_rows):
        if len(row) < 2 * n_ports * n_ports:
            raise ValueError(
                f"row {i}: expected {2 * n_ports * n_ports} numeric values, got {len(row)} "
                f"(file {path})"
            )
        vals = np.asarray(row[: 2 * n_ports * n_ports], dtype=float)
        if option_format == "MA":
            mag = vals[0::2]
            ang = np.deg2rad(vals[1::2])
            block = mag * np.exp(1j * ang)
        elif option_format == "DB":
            mag = 10.0 ** (vals[0::2] / 20.0)
            ang = np.deg2rad(vals[1::2])
            block = mag * np.exp(1j * ang)
        elif option_format == "RI":
            re = vals[0::2]
            im = vals[1::2]
            block = re + 1j * im
        else:  # pragma: no cover - option line validated above
            raise ValueError(f"unknown format {option_format!r}")
        s[i] = _decode_s_matrix(block, n_ports)

    if not option_line_seen:
        comments.append(
            "no Touchstone option line ('# ...') found; assuming spec defaults "
            "(GHz, S, MA, R 50) — frequency values are interpreted as GHz (ERR-005)"
        )

    # NEW-007: a ``! Reference Impedance <value>`` comment overrides the
    # option-line R when it is a single scalar. The Touchstone spec assigns z0
    # to the option line, but many EDA tools (and the IBIS/AMI de-facto usage)
    # write the *actual* termination into a comment because the option line R
    # only holds one value. Honor the comment so TDR/mixed-mode analysis
    # references the real system impedance instead of silently staying at 50Ω.
    ref_z = _ref_impedance_from_comments(comments)
    final_z0 = option_z0
    if ref_z is not None and ref_z > 0:
        final_z0 = ref_z

    return Touchstone(
        path=str(path),
        n_ports=n_ports,
        freqs=freqs,
        s=s,
        z0=final_z0,
        format=option_format,
        freq_unit=option_freq_unit,
        comments=comments,
    )


class PreflightIssue(str):
    """A preflight warning string tagged with a severity level (ERR-011).

    Subclasses :class:`str` so existing callers that do ``"msg" in w[0]`` or
    ``any("msg" in s for s in w)`` keep working unchanged; the ``level``
    attribute lets the pipeline block on fatal issues instead of silently
    continuing into invalid metrics.

    Levels:
    - ``"ERROR"`` — fatal; the data cannot produce trustworthy metrics
      (NaN/Inf, port-count mismatch, no frequency points). The pipeline must
      not proceed to compute_metrics/TDR (ERR-011).
    - ``"WARN"`` — recoverable but suspect (non-monotonic freq, sweep narrower
      than the acceptance band). Escalated to ERROR by the caller when TDR is
      enabled, since the irfft grid requires monotonic data.
    """

    level: str

    def __new__(cls, message: str, level: str = "WARN") -> "PreflightIssue":
        obj = super().__new__(cls, message)
        obj.level = level
        return obj


def check_port_and_freq(
    ts: Touchstone,
    *,
    expected_ports: int | None = None,
    fmin: float | None = None,
    fmax: float | None = None,
    require_monotonic: bool = True,
    required_ports: list[int] | None = None,
    strict: bool = False,
) -> list[PreflightIssue]:
    """Preflight checks: port count, frequency range, monotonicity (§10.3).

    Returns a list of :class:`PreflightIssue` (str subclasses) tagged with a
    severity ``level`` — ``"ERROR"`` for fatal conditions that would produce
    invalid metrics (NaN/Inf, port mismatch, no freq points), ``"WARN"``
    otherwise (ERR-011). An empty list means all checks passed.

    Port-count policy (NEW-008): historically the check demanded an *exact*
    match between the file's port count and ``expected_ports`` (the number of
    mapped single-ended ports). That blocked the common workflow of analyzing
    a subset of ports from a larger ``.sNp`` file — e.g. mapping ports 1–4 of
    an ``.s8p`` to study one lane of a connector. The file is now allowed to
    have *more* ports than the mapping needs (a WARN, not an ERROR), provided
    every port the mapping references actually exists (the mode-matrix builder
    would index out-of-range ports and produce garbage otherwise). Two ways to
    express the requirement:

    - ``expected_ports`` — the number of mapped ports (``2*n_pairs``). Now
      treated as a *minimum* (file must have >= this many ports). ``strict``
      re-enables the legacy exact-match behavior for callers that want to
      catch an under- or over-sized file.
    - ``required_ports`` — an explicit list of 1-based port indices the mapping
      references. When given, every index must satisfy ``1 <= idx <= n_ports``;
      a missing port is an ERROR. This is what the CLI/batch pass so a 4-port
      mapping into an 8-port file passes while a mapping that references port
      9 of an 8-port file fails.
    """
    warnings: list[PreflightIssue] = []
    if expected_ports is not None:
        if strict:
            # Legacy exact-match: file port count must equal the mapping size.
            if ts.n_ports != expected_ports:
                warnings.append(
                    PreflightIssue(
                        f"strict mode: expected exactly {expected_ports} "
                        f"ports, file has {ts.n_ports}",
                        level="ERROR",
                    )
                )
        elif ts.n_ports < expected_ports:
            # Fewer ports than the mapping needs is always fatal — the mapping
            # references ports that don't exist.
            warnings.append(
                PreflightIssue(
                    f"mapping needs {expected_ports} ports, file has only "
                    f"{ts.n_ports}",
                    level="ERROR",
                )
            )
        elif ts.n_ports > expected_ports:
            # NEW-008: extra unmapped ports are fine — the mode-matrix builder
            # only touches the mapped columns/rows. Warn so the user knows the
            # file carries data the analysis ignores.
            warnings.append(
                PreflightIssue(
                    f"file has {ts.n_ports} ports but mapping uses only "
                    f"{expected_ports}; extra ports ignored (NEW-008)"
                )
            )
    # Explicit per-port existence check (NEW-008). This is the strict
    # correctness guard: a mapping that references port 9 of an 8-port file
    # must fail regardless of the >= minimum, because the mode matrix would
    # index a non-existent column.
    if required_ports:
        missing = [p for p in required_ports if p < 1 or p > ts.n_ports]
        if missing:
            warnings.append(
                PreflightIssue(
                    f"mapping references port(s) {missing} which do not exist "
                    f"in a {ts.n_ports}-port file",
                    level="ERROR",
                )
            )
    if ts.freqs.size == 0:
        warnings.append(PreflightIssue("no frequency points", level="ERROR"))
    else:
        f_lo = float(ts.freqs.min())
        f_hi = float(ts.freqs.max())
        if fmin is not None and f_lo > fmin:
            warnings.append(
                PreflightIssue(
                    f"lowest frequency {f_lo:.3e} Hz is above the required {fmin:.3e} Hz"
                )
            )
        if fmax is not None and f_hi < fmax:
            warnings.append(
                PreflightIssue(
                    f"highest frequency {f_hi:.3e} Hz is below the required {fmax:.3e} Hz"
                )
            )
        if require_monotonic:
            diffs = np.diff(ts.freqs)
            if np.any(diffs <= 0):
                # Non-monotonic frequencies corrupt the irfft grid; WARN by
                # default, escalated to ERROR by the caller when TDR is on.
                warnings.append(
                    PreflightIssue("frequencies are not strictly increasing")
                )
    # NaN/Inf in the raw data is always fatal: np.max/np.min would produce
    # NaN, every acceptance comparison would be False, and the report would
    # mislabel a corrupt file as FAIL rather than ERROR (ERR-011).
    for w in _check_invalid_values(ts.freqs, ts.s, ts.path):
        warnings.append(PreflightIssue(w, level="ERROR"))
    return warnings


class PreflightError(ValueError):
    """Raised when preflight finds a fatal (ERROR-level) condition (ERR-011).

    Carries the list of ERROR-level issues so the caller (batch isolation, CLI
    error report) can surface them verbatim instead of crashing with a raw
    traceback. ``ValueError`` is the base so legacy ``except ValueError`` paths
    still catch it.
    """

    def __init__(self, errors: list[PreflightIssue]) -> None:
        self.errors = errors
        super().__init__(
            "fatal preflight issues:\n" + "\n".join(f"  - {e}" for e in errors)
        )


def split_preflight(
    issues: list[PreflightIssue],
    *,
    tdr_enabled: bool = False,
) -> tuple[list[PreflightIssue], list[PreflightIssue]]:
    """Split preflight issues into ``(errors, warnings)`` (ERR-011).

    ERROR-level issues block analysis (NaN/Inf, port-count mismatch, no
    frequency points). WARN-level issues are surfaced in the report but do not
    block.

    Context-sensitive escalation: non-monotonic frequencies are WARN for
    frequency-domain metrics (which tolerate them) but are escalated to ERROR
    when TDR is enabled, because :func:`numpy.fft.irfft` requires a strictly
    increasing uniform grid — feeding it non-monotonic data silently produces a
    garbage impedance trace (ERR-011 / I-04).
    """
    errors: list[PreflightIssue] = []
    warns: list[PreflightIssue] = []
    for issue in issues:
        level = issue.level
        if tdr_enabled and "not strictly increasing" in issue:
            issue = PreflightIssue(str(issue), level="ERROR")
            level = "ERROR"
        if level == "ERROR":
            errors.append(issue)
        else:
            warns.append(issue)
    return errors, warns
