# HFSS em-copilot 精简交付包 — 说明

本包由 `HFSS.zip`（原始压缩包 SHA-256 见 `SHA256SUMS.txt` 与 `DELIVERY-NOTES.md`）清洗生成，只保留可复现的工程资产。

## 内容
- `src/em_copilot/` — 确定性 SI 分析核心（Touchstone 解析、混模转换、指标/TDR、报告）
- `tests/` — 5 个测试模块（离线、无需 HFSS 许可证）
- `docs/`、`PLAN-*.md`、`README.md` — 方案、路线图、运行手册、能力缺口、版本清单
- `scripts/`、`skills/` — 环境引导、MCP 调用、演示驱动与领域 Skill
- `examples/golden/` — 三个精选黄金案例（同轴、阶跃同轴、Twinax），含输入、Touchstone、指标、报告
- `mcp.json.template` — 可移植 MCP 配置模板（原绝对路径已移除）

## 环境重建
本项目要求 Python 3.12+。依赖为纯 Python 轮子（numpy/scipy/matplotlib/pydantic），离线分析与测试**不需要** HFSS 或 AEDT 许可证：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e . --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org
```

企业内网需先设置代理，参见 `README.md` 的安装章节。接入 AEDT 时才需安装 `requirements-cloud.txt` 中的 `pyaedt` / `ansys-aedt-mcp`。

## 运行离线验证
```powershell
pytest tests -q
em-copilot demo --out runs/demo
```

## MCP 配置
把 `mcp.json.template` 复制为 `.mcp.json`，并将 `${EM_COPILOT_ROOT}` 替换为本机项目根目录。原压缩包内嵌的 `D:\HCX\HFSS\em-copilot` 绝对路径**未**随包交付，以避免环境耦合。

## 前置条件与已知限制
- 实时 HFSS 求解需要在有许可证的 Windows + AEDT 2025.2 环境进行；本包不做求解。
- 黄金案例的 `compliance` 均为 `NOT_EVALUATED`——尚无量化验收门限，见 `examples/golden/README.md`。
- 大型原始求解证据（`.aedtresults`、会话树、日志、探针）**不在本包内**，已移入独立取证归档。
- 本包不再携带 `.venv`、字节码与缓存，需按上述步骤重建环境。