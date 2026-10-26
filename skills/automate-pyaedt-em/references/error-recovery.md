# Error Recovery

> Roadmap §15 / §16.4. Specific recovery paths for common AEDT/MCP failures.
> Load this reference when a tool call returns an error.

## 1. License / launch failures

**Symptom**: `launch_aedt` or `connect_to_aedt` fails; message about license checkout, "no licenses available", or AEDT process not starting.

Recovery:
1. `check_aedt_installed` — confirm the binary exists.
2. `check_aedt_status` — see if a session is already running you can attach to instead of launching new.
3. If a session is running, prefer `connect_to_aedt` (attach) over `launch_aedt` (new) to avoid a second license.
4. If no license is available, stop and report; do not retry in a loop — each retry may hold a checkout briefly.
5. Confirm with the user before any further launch attempt.

## 2. Orphan processes

**Symptom**: A previous solve or session crashed but left an `ansysedt.exe` / `ansys.engine` process; new launches behave oddly or claim a port is in use.

Recovery:
1. `check_aedt_status` — list running sessions; identify orphan by PID and gRPC port.
2. Attempt `disconnect_from_aedt` on the orphan session (clean teardown).
3. If that fails, ask the user (with the PID) before killing the process externally; killing AEDT externally can corrupt a `.aedt` lock file.
4. Remove the `.lock` file in the project directory only after confirming no live AEDT owns it.
5. Record the orphan in the report (root cause: crash? timeout? lost connection?).

## 3. Solve timeout

**Symptom**: a solve exceeds its timeout (Job sub-state `TIMED_OUT`).

Recovery:
1. `get_analysis_log` — retrieve the last lines to see where it stalled (mesh pass N? a specific frequency?).
2. If it stalled on mesh passes → geometry/mesh problem (see `mesh-and-convergence.md`); reduce max passes or clean geometry, don't just raise the timeout.
3. If it stalled mid-sweep → possibly a single bad frequency point; consider a discrete sweep around the bad point.
4. Do **not** blindly raise the timeout and retry. Diagnose first.
5. If diagnosis shows it simply needs more time, raise the timeout and resubmit via `submit_analysis` (new `job_id`).

## 4. Mesh failure / non-convergence

**Symptom**: max passes reached, ΔS never under threshold (Job sub-state `FAILED` or a converged-but-suspicious result).

Recovery:
1. Inspect the final mesh and the convergence history (ΔS per pass).
2. If ΔS is trending down but slowly → raise max passes.
3. If ΔS is flat / oscillating → geometry/mesh issue: sliver tets, sharp features, thin layers. Clean geometry or add mesh operations.
4. Re-run a **smoke solve** (2–3 passes) after the change to confirm the fix before a full solve.
5. Never report a non-converged result as final.

## 5. Port / setup errors

**Symptom**: solver reports a port error, "no port found", "degenerate mode", or S-parameters are obviously unphysical (Sdd21 > 0 dB for a through, Sdd11 = 0 dB).

Recovery:
1. Re-list ports with `get_model_info`; confirm each port's type, reference conductor, and location.
2. Check wave-port sizing (too small → degenerate, too large → spurious modes) — see `boundaries-and-ports.md`.
3. Confirm the reference conductor is a ground/reference net, not a signal net.
4. Fix and re-run the smoke solve.

## 6. MCP / stdio connection cut

**Symptom**: Claude loses contact with the `pyaedt-em` MCP server (tools stop responding, connection reset).

Recovery:
1. Check the MCP server's env: **proxy must be cleared** (`HTTP_PROXY=""`, `HTTPS_PROXY=""`, `NO_PROXY="*"`). The enterprise proxy cuts stdio.
2. Check the AEDT session is still alive: `check_aedt_status` after reconnecting.
3. If the AEDT session died but the solve was submitted as a Job, the Job is `ORPHANED` — poll `get_analysis_status`; if the solver process is still running standalone, results may still be collectable; otherwise resubmit.
4. Do not assume a tool failure means a solve failure — the solve runs in the AEDT process, independent of the MCP stdio connection.

## 7. Disk / output errors

**Symptom**: `export_results` / `save_project` fails to write; permission denied; path not found.

Recovery:
1. Confirm the output directory exists and is writable; outputs go to the **task run dir**, never back to the input directory.
2. Confirm the input `.aedt` was copied (copy-before-run) and the copy is what's being written to.
3. Do not write to the input directory. If the only writable path is the input dir, stop and ask the user.

## 8. General principle

On any error: stop the automation sequence, summarize what failed and the last known good state, propose the **smallest safe recovery**, and get user confirmation before any write-side or launch action. Never blindly retry the same call.
