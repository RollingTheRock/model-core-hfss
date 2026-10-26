# 交接：pyaedt-em 全流程 demo（Twinaxial_cable）

> 生成于本对话（会话启动早于 MCP 配置写入，本会话调不到 MCP 工具）。
> 另一对话已 /mcp connected pyaedt-em，切过去继续 §6 全流程。读此文件接上。

## 环境已就绪（全部完成，勿重做）

- Python 3.12.10（embeddable, `C:\Users\h50012464\py312\python312\python.exe`，MSI 被组策略封，用 uv 绕开）
- .venv: `D:\HCX\HFSS\em-copilot\.venv`（Python 3.12.10，uv 建）
- em-copilot-core editable 已装；requirements-cloud.txt 已装（pyaedt 1.5.0 / ansys-aedt-mcp 0.1.4 / fastmcp 4.0.2 / mcp 2.1.1 / typer 0.27.2）
- **envcheck 全绿 exit 0**：19 PASS / 0 fail / 0 warn / 0 blocked；`aedt.install` PASS（AEDT 2025.2）
- envcheck JSON 存于 `em-copilot/docs/_envcheck_cloud.json`
- .mcp.json 在 `D:\HCX\HFSS\.mcp.json` + `em-copilot\.mcp.json`（内容同），并写入 `.claude.json` projects["D:/HCX/HFSS"].mcpServers
- 代理：pip 用 `http://192.168.127.73:8080`；MCP server env 必须清空代理（HTTP_PROXY="" HTTPS_PROXY="" NO_PROXY="*"）

## 选定 demo 工程

- 原始（只读）：`C:\Program Files\ANSYS Inc\v252\AnsysEM\Examples\HFSS\Transmission Lines\Twinaxial_cable.aedt`
- **副本（open_project 指向这个）**：`D:\HCX\HFSS\em-copilot\runs\twinaxial-demo-001\Twinaxial_cable_COPY.aedt`
- input SHA-256: `61c1eb7d259c2b65bf9b6ee560f3063d38913f0970006efae44aec1315d1c279`
- 离线 Preflight：DesignName=`Twinaxial_cable_28AWG`，SolutionType=`HFSS Hybrid Terminal Network`，2 wave port (Terminal 模式 = 4 端口差分对)，Sweep 0–10GHz LinearStep 50MHz (200 点)，InterpTolerance=0.5
- 无 .results 子目录 = 干净未求解，适合 smoke→full

## §6 全流程剩余步骤（MCP 工具，在那个对话调）

已确认：check_aedt_installed → AEDT 2025.2 installed；check_aedt_status → connected:false, 0 sessions（需 launch/connect）

1. **launch_aedt**（L3 占 license，用户已授权走非图形）。连不上 license 立刻停、汇报。
   - 建议参数：`{"non_graphical": true}` 或按工具签名
2. **open_project** → 指向副本 `D:\HCX\HFSS\em-copilot\runs\twinaxial-demo-001\Twinaxial_cable_COPY.aedt`（绝不指原始）
3. **list_designs** + **get_model_info** → 只读检视（L0）
4. **get_guidelines_for** → 取 HFSS geometry/mesh/boundaries 指南（需 --include-context，已配）
5. **analyze_design**(blocking=False) → 先 smoke（2-3 频点），通过再 full
6. **export_results** → 导出 .s4p 到 `runs\twinaxial-demo-001\`（不写回输入目录）
7. `em-copilot analyze <导出.s4p> --pair TX0 1 2 --pair RX0 3 4 --tdr --near-pair TX0 --far-pair RX0 --out runs\twinaxial-demo-001\report`
   （Twinaxial 是差分对，mixed-mode + TDR；验收指标待和用户定，可先不加 --accept 跑通）
8. **save_project** + **disconnect_from_aedt** → 确认 PID 释放 + license 归还

## 工具直调（如需在无 MCP 连接的会话里验证）

- `em-copilot\scripts\mcp-tool-call.py <tool> [json_args]`：stdio 直调单工具（每次新进程，launch 的连接不持久——仅适合离线工具如 check_aedt_installed/status）
- `em-copilot\scripts\mcp-handshake-selftest.py`：握手自测（已验证 PASS）

## 注意

- run_python_code / run_python_script 是 L4，仅 admin 诊断，不是业务路径
- 数值纪律：报告数值由 em_copilot 确定性代码生成，AI 只写解释不改数
- license 风险：launch/connect 占一个 HFSS license，disconnect 后确认归还
