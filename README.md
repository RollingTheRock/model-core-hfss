# em-copilot-core

企业电磁仿真辅助平台的领域包（独立命名空间 `em_copilot`）。

> **命名空间纪律**：本包不占用 `ansys.aedt.mcp` 官方命名空间。官方 MCP（`ansys-aedt-mcp`，PyPI 0.1.4，GitHub `ansys/pyaedt-mcp`）作为 Agent 工具层使用；本包只做**不依赖 HFSS 许可证**的领域工作流——第一阶段是 **Touchstone / S 参数自动分析**（路线图 §10，可 CI、可公开演示）。

## 现阶段范围（V0.1 MVP）

路线图 §21 / §10 的最小可演示子集：

- Touchstone `.sNp` 读取（支持 MA / dB / 实部虚部，Hz/kHz/MHz/GHz，所有端口使用相同实数参考阻抗；每端口/复数/频变参考阻抗暂不支持，仅从选项行 `R` 及 `! Reference Impedance` 注释读取单一实数 Z0）
- 单端 → 混合模式转换（可配置差分对映射）
- 指标：差分回损 `Sdd11`、差分插损 `Sdd21`/`Sdd12`（正向/反向）、模式转换 `Sdc21`/`Scd21`（正反向）、（多对时）NEXT/FEXT、指定频点结果、裕量
- 三态合规判定：`PASS` / `FAIL` / `NOT_EVALUATED`（无规则不误报失败）/ `ERROR`
- 可选 TDR（逆 FFT）
- Markdown + PNG 报告（数值由代码生成，不含 AI 幻觉）
- 合成演示 `.s4p`，无需商业求解器即可端到端跑通

## 不在现阶段范围

- 任意 CAD 自动建模、自动端口推断
- HFSS 求解（走官方 MCP，后续阶段接入）
- 企业调度平台、权限、审计

## 安装

```powershell
# 企业内网需先设代理 + 信任主机
$env:HTTP_PROXY  = "http://proxy.xfusion.com:8080"
$env:HTTPS_PROXY = "http://proxy.xfusion.com:8080"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e . --trusted-host pypi.org `
    --trusted-host files.pythonhosted.org --trusted-host pypi.python.org
```

依赖：`numpy>=1.26`、`scipy>=1.11`、`matplotlib>=3.7`、`pydantic>=2`。全部为纯 Python 轮子，CI 友好，无需 HFSS / AEDT 许可证。

## 用法

### 一键演示（合成 .s4p → 报告）

```powershell
em-copilot demo --out runs/demo
```

输出：

```
runs/demo/
├── demo_channel.s4p      # 合成 4 端口 Touchstone（真实格式，演示用）
├── report.md             # 9 节 Markdown 报告（含图、表、JSON）
├── metrics.json          # 机器可读结构化结果
└── plots/
    ├── s_parameters.png  # Sdd21 / Sdd11 / Scd21
    └── tdr.png           # 逆 FFT 阻抗估计
```

### 分析真实 Touchstone

```powershell
em-copilot analyze my_channel.s4p `
    --pair TX0 1 2 --pair RX0 3 4 `
    --accept "Sdd21 >= -12dB 16GHz" `
    --accept "Sdd11 <= -10dB 4GHz" `
    --poi 4GHz --poi 16GHz --tdr --out runs/analyze
```

- `--pair NAME POS NEG`：差分对，端口 1-based，可重复（定义多对链路）。
- `--accept "EXPR OP VALUE FREQ"`：验收规则，可重复。`EXPR ∈ {Sdd11,Sdd21,Sdc21,Scd21,Scc11,Scc21,NEXT,FEXT,IL_margin,RL_margin}`；`OP ∈ {>=,<=,>,<,==}`；`VALUE` 如 `-12dB`；`FREQ` 如 `16GHz`。
- `--poi FREQ`：关注频点，可重复。
- `--tdr`：计算逆 FFT TDR 阻抗。
- 退出码三态：`0` = PASS / NOT_EVALUATED，`1` = FAIL，`2` = ERROR（可用于 CI 流水线门禁；纯分析无规则不会挂红）。

## 报告结构（路线图 §13.2 / §13.3）

| 节 | 内容 |
|---|---|
| 1 | 任务摘要（任务 ID、验收结果、输入哈希、频率范围） |
| 2 | 输入文件与版本（端口数、格式、Z0、差分对） |
| 3 | Preflight 检查（端口数 / 频率范围 / 单调性 / NaN-Inf） |
| 4 | S 参数结果（图 + worst-case 表） |
| 5 | 指定频点结果（关注频点全指标快照） |
| 6 | 串扰（≥3 差分端口时 NEXT/FEXT，否则说明不适用） |
| 7 | TDR / 阻抗结果（可选） |
| 8 | 验收规则（表达式 / 频率 / 限值 / 实测 / 通过） |
| 9 | 可复现信息（完整 JSON，同时写入 `metrics.json`） |

> **数值由程序生成，AI 只生成解释**：报告中所有数值由 `em_copilot` 确定性代码产生，未经 AI 修改；`metrics.json` 是同一份数据的机器可读形式，便于流水线消费。

## 模块

| 文件 | 职责 |
|---|---|
| `touchstone.py` | `.sNp` 读取 + preflight |
| `schemas.py` | Pydantic 任务 / 差分对 / 验收规则模型 |
| `mixed_mode.py` | Bockelman–Dixon–Wilson 变换 + SI 指标 |
| `analysis.py` | 验收规则评估 + TDR |
| `reporting.py` | Markdown + PNG + JSON 渲染 |
| `demo.py` | 合成 4 端口差分通道 `.s4p` 生成器 |
| `cli.py` | `em-copilot demo` / `em-copilot analyze` 入口 |

## 测试

```powershell
em-copilot demo --out runs/demo
em-copilot analyze runs/demo/demo_channel.s4p `
    --pair TX0 1 2 --pair RX0 3 4 `
    --accept "Sdd21 >= -12dB 16GHz" --tdr --out runs/analyze
```

两条命令均应退出码 `0`、生成 `report.md` + `metrics.json` + PNG 图。

