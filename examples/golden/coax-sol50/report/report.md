# 单端传输线 S 参数报告 — coax_sol50_Design

> 生成时间由程序确定。数值由确定性代码生成，AI 仅生成解释。
> 路线图 §13.3：计算结果与 AI 建议明确分离。

## 0. 模型概览

![Model Preview](model_preview.jpg)

> 图 0：coax_sol50_Design 设计 3D 模型预览（求解前）。

## 1. 任务摘要

- **任务 ID**：coax_sol50_Design-single
- **类型**：单端 / Touchstone 自动分析（coax / microstrip）
- **设计**：coax_sol50_Design（模板 coax）
- **输入文件**：`D:\HCX\HFSS\em-copilot\demo_output\run_coax_sol50\coax_sol50_Design_sweep.s2p`
- **输入 SHA-256（前 16 位）**：`1125d175e8155957`
- **频率范围**：1.000 GHz ~ 3.000 GHz
- **频率点数**：11
- **端口数**：2

## 2. 输入文件与版本

- Touchstone 端口数：2
- 原始格式：MA
- 原始频率单位：ghz
- 参考阻抗 Z0：50.0 Ω

## 3. Preflight 检查

- ✅ 端口数、频率范围、单调性、数值有效性检查全部通过

## 4. S 参数结果

![S-parameters](plots/s_parameters.png)

> 图 1：coax_sol50_Design 单端 S 参数（S11 回损 @端口1 / S21 插损 1→2）。

### 4.1 最差情形（worst case）

| 指标 | 最差值 |
|---|---|
| S11_worst_dB | -0.010 |
| S11_worst_freq_GHz | 3.000 |
| S21_worst_dB | -224.930 |
| S21_worst_freq_GHz | 1.000 |
| S11_min_over_band_dB | -0.010 |

## 5. 网格收敛

![Convergence](plots/convergence.png)

> 图 2：自适应网格细化收敛过程（蓝色柱：四面体数；橙色线：ΔS 收敛值）。

- Adaptive Pass 1：6666 四面体，ΔS=N/A
- Adaptive Pass 2：8638 四面体，ΔS=8.25e-02
- Adaptive Pass 3：11164 四面体，ΔS=1.82e-02

## 6. 指定频点结果

| 频率 | S11 (回损, dB) | S21 (插损, dB) |
|---|---|---|
| 1.000 GHz | -0.01 dB | -224.93 dB |
| 3.000 GHz | -0.01 dB | -202.04 dB |

## 7. 串扰

- 本模板为单端 2 端口链路，无独立串扰对，NEXT/FEXT 不适用（需 ≥3 个差分端口才有独立串扰对）。

## 8. TDR / 阻抗结果

- 单端模板未计算 TDR（需差分对/端口对才能估计共模阻抗）。

## 9. 验收规则

（未定义验收规则 — 仅分析，未做合规判定）

## 10. 可复现信息

```json
{
  "task_id": "coax_sol50_Design-single",
  "touchstone": "D:\\HCX\\HFSS\\em-copilot\\demo_output\\run_coax_sol50\\coax_sol50_Design_sweep.s2p",
  "design_name": "coax_sol50_Design",
  "template": "coax",
  "kind": "single",
  "worst_case": {
    "S11_worst_dB": -0.01,
    "S11_worst_freq_GHz": 3.0,
    "S21_worst_dB": -224.93,
    "S21_worst_freq_GHz": 1.0,
    "S11_min_over_band_dB": -0.01
  },
  "points_of_interest": [
    [
      "1.000 GHz",
      "-0.01 dB",
      "-224.93 dB"
    ],
    [
      "3.000 GHz",
      "-0.01 dB",
      "-202.04 dB"
    ]
  ],
  "acceptance": [],
  "warnings": [],
  "input_hash": "1125d175e8155957",
  "n_freqs": 11,
  "freq_range_hz": [
    1000000000.0,
    3000000000.0
  ],
  "n_ports": 2
}
```

- 结构化结果同时写入 `metrics.json`。
- 本报告中所有数值由 `em_copilot` 确定性代码生成，未经 AI 修改。
