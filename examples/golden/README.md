# 黄金案例说明（examples/golden）

本目录是**受控的回归契约**，不是分析工作目录的副本。每个案例包含：可再打开/复算的 HFSS 输入、紧凑的 Touchstone 结果、机器可读指标、人读报告，以及该案例验证什么的说明。

所有案例均来自 HFSS 2025.2 导出。`compliance` 字段均为 `NOT_EVALUATED`——**尚无语料验收门限被定义**，因此这些结果是基线记录，不是工程合格判定。

## coax-sol50
- **验证内容**：固定模板同轴结构的双端口 S 参数导出与后处理基线。
- **输入**：`coax_sol50.aedt`（Design `coax_sol50_Design`）。
- **结果**：`coax_sol50_Design_sweep.s2p`，`metrics.json`（11 点，1–3 GHz，2 端口）。
- **指标快照**：`S11_worst_dB = -0.01`，`S21_worst_dB = -224.93`。
- **说明**：轻量、单端、适合作为 CI 中最快的真实导出回归样本。

## coax-step
- **验证内容**：带阶跃变量的同轴变体，覆盖参数化几何与模板改动。
- **输入**：`coax_stepv2.aedt`。
- **结果**：`coax_step_Design_sweep.s2p`（2 端口）。
- **注意**：该目录**没有** `metrics.json`；如纳入自动回归，需先用交付包内的分析路径重新生成一份基线指标。

## twinax-28awg
- **验证内容**：当前 MVP 主案例。4 端口差分对（Terminal 模式 2 波端口 → Pair1/Pair2），0–10 GHz / 201 点全扫频导出，混模转换与 TDR 后处理。
- **输入**：`Twinaxial_cable_COPY.aedt`（Design `Twinaxial_cable_28AWG`，SolutionType `HFSS Hybrid Terminal Network`）。
  - 来源：AEDT 自带示例 `Twinaxial_cable.aedt` 的副本；原始输入 SHA-256 `61c1eb7d259c2b65bf9b6ee560f3063d38913f0970006efae44aec1315d1c279`（见 `HANDOFF.md`）。
- **结果**：`Twinaxial_cable_28AWG_sweep.s4p`（201 点全扫频）、`Twinaxial_cable_28AWG_full.s4p`。
- **指标快照**（`report/metrics.json`，输入哈希 `6af72a193d0f4a90`）：
  - `Sdd11_dB_worst = -88.14`，`Sdd21_dB_worst = -0.0966`（@10 GHz）
  - 模式转换 `Scd21/Sdc21_dB_worst = -36.74`
- **说明**：该案例的完整求解原始结果（`.aedtresults`、`.pyaedt` 会话树）不在交付包内，已移入独立取证归档；如需追溯场/网格/收敛过程，请查取证包 `raw-solver-results/`。

## 使用方式

无需 HFSS 许可证即可复核任一案例的结果：

```bash
em-copilot analyze examples/golden/twinax-28awg/Twinaxial_cable_28AWG_sweep.s4p \
  --pair Pair1 1 2 --pair Pair2 3 4 --tdr --out <输出目录>
```

需要 AEDT 参与时，请在**有许可证的 Windows 环境**中打开对应 `.aedt`；这些工程在迁移后能否直接打开尚未逐一验证（见 `DELIVERY-NOTES.md` 的验证状态）。