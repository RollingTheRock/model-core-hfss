# MVP 计划：AI 编排的 HFSS → S 参数 → 报告 全流程演示

## 目标

由 AI 助手在对话中编排触发，每一步调用什么工具都可见，从已有的
Twinaxial_cable HFSS 工程一路走到完整 201 频率点 S 参数文件 + SI 分析报告。
全程不靠 AI 现场写仿真代码、不出现连接障碍（独立进程 attach 已证实走不通）。

## 架构决策（基于方案 A 验证结果）

独立 server attach AEDT 这条路已证伪（干净 HFSS 上仍卡死）。
采用"工具函数库 + 官方 run_python_code 调用"方向：

- **工具代码**：固定写死在 `em_copilot.aedt_ops` 模块里，有版本管理、可单测、
  AI 不改动。这正是用户"代码固定存本地"的诉求。
- **执行方式**：通过官方 `run_python_code` 调一行 import + 调用，
  官方进程注入已持有的 `desktop` 句柄，不自己 attach（绕开连接障碍）。
- **传给 run_python_code 的内容**：只有"调用语句"（import + 函数调用 + 参数），
  不是业务逻辑——生成错误风险极低，且代码体可预先校验。

## 关键约束

1. `run_python_code` 注入 `desktop`（PyAEDT Desktop 实例）、`odesktop`、`aedt_port`。
   工具函数签名必须接收 `desktop` 参数，由调用语句传入。
2. 官方 server 必须先 `launch_aedt` + `open_project`，保持 connected 状态，
   这样 `desktop` 句柄有效、工程已加载。
3. 导出的 s4p 不能写回输入工程目录（§14 安全：输出与输入分离）。
4. 报告数值由 em_copilot 确定性代码生成，AI 只写解释（§13.3）。

## MVP 演示流程（8 步，AI 编排，每步可见）

| 步骤 | 调用 | 角色 | 产物 |
|------|------|------|------|
| 1 | `launch_aedt` | 官方工具 L3 | AEDT 进程 + license |
| 2 | `open_project` | 官方工具 L1 | 加载 Twinaxial_cable_COPY.aedt |
| 3 | `list_designs` | 官方工具 L0 | 确认 design=Twinaxial_cable_28AWG |
| 4 | `run_python_code`（调 em_copilot.aedt_ops.list_setups_and_sweeps） | 自研工具函数 | 列出 setup/sweep 名（确认 "3GHz"+"DC_10GHz"） |
| 5 | `run_python_code`（调 em_copilot.aedt_ops.export_touchstone_sweep） | 自研工具函数 | 201 频率点 s4p 文件 |
| 6 | `em-copilot analyze` CLI（或 run_python_code 调分析函数） | em_copilot 离线 | metrics.json + S 参数图 |
| 7 | 报告渲染 | em_copilot 离线 | report.md + TDR 图 |
| 8 | `disconnect_from_aedt` | 官方工具 | license 归还 |

## 新增文件 / 修改

### 新增 `src/em_copilot/aedt_ops.py`（核心：固定工具函数库）

这是"工具代码存本地"的落点。每个函数接收 `desktop` 句柄，纯函数、可单测、
AI 永远只调不改。

```python
"""em-copilot 固定 AEDT 工具函数库（roadmap §8.2 差距-1）。

这些函数由 AI 通过官方 ``run_python_code`` 调一行 import 来执行——
官方进程注入已持有的 ``desktop`` 句柄，不自己 attach AEDT
（独立进程 attach 已证伪，见 desktop-grpc-attach-hang 记忆）。

设计原则：
- 纯函数：接收 desktop + 参数，返回结构化结果，无副作用之外的隐藏状态。
- 可单测：逻辑不依赖 AEDT 时可 mock desktop 测试。
- AI 永远只调用，不修改本文件的业务逻辑。
- 数值纪律：返回原始数值，不做 AI 可篡改的解释。
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any


def list_setups_and_sweeps(desktop, project_name=None, design_name=None) -> str:
    """列出当前 design 的所有 setup 及每个 setup 的 sweep 名。
    返回 JSON：{design, setups: [{name, sweeps: [...]}]}。
    AI 用这个确认 setup_name/sweep_name，避免传错。
    """
    from ansys.aedt.core import get_pyaedt_app
    app = get_pyaedt_app(project_name=project_name, design_name=design_name, desktop=desktop)
    setups = []
    for s in app.setups:
        sweeps = s.get_sweep_names()
        setups.append({"name": s.name, "sweeps": sweeps})
    return json.dumps({"design": app.design_name, "setups": setups}, ensure_ascii=False, indent=2)


def export_touchstone_sweep(desktop, setup_name, sweep_name, output_file,
                             project_name=None, design_name=None) -> str:
    """导出频率扫描 Touchstone（非 LastAdaptive 单点）。
    校验 setup/sweep 存在，调 app.export_touchstone(setup=, sweep=)，
    后校验 >1 频率点。返回 JSON：{success, output_file, n_freqs, n_ports, setup, sweep}。
    """
    from ansys.aedt.core import get_pyaedt_app
    app = get_pyaedt_app(project_name=project_name, design_name=design_name, desktop=desktop)
    # 校验 setup
    setup_names = [s.name for s in app.setups]
    if setup_name not in setup_names:
        return json.dumps({"success": False, "error": f"setup {setup_name!r} not in {setup_names}"})
    setup = next(s for s in app.setups if s.name == setup_name)
    sweep_names = setup.get_sweep_names()
    if sweep_name not in sweep_names:
        return json.dumps({"success": False, "error": f"sweep {sweep_name!r} not in {sweep_names}"})
    # 导出（setup=, sweep= → 真正的频率扫描，非 LastAdaptive）
    app.export_touchstone(setup=setup_name, sweep=sweep_name, output_file=output_file)
    # 后校验：频率点数
    n_ports = len(app.port_list or []) or _guess_ports(output_file)
    n_freqs = _count_freqs(output_file, n_ports)
    return json.dumps({
        "success": True, "output_file": str(Path(output_file).resolve()).replace("\\", "/"),
        "n_freqs": n_freqs, "n_ports": n_ports, "setup": setup_name, "sweep": sweep_name,
    }, ensure_ascii=False, indent=2)


def _guess_ports(path):  # .s4p → 4
    import re
    m = re.search(r"\.s(\d+)p", Path(path).name, re.IGNORECASE)
    return int(m.group(1)) if m else 2

def _count_freqs(path, n_ports):
    p = Path(path)
    if not p.is_file(): return 0
    import re
    rpf = max(n_ports, 1); rows = 0
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith(("!","#")) and re.match(r"^[+-]?[0-9.]", s):
                rows += 1
    return max(rows // rpf, 0)
```

### 修改 `src/em_copilot/mcp/tools.py`

把原 `export_touchstone_sweep` 工具改为 thin wrapper：不再自己 `_connect_aedt`
（已证伪），而是委托 `em_copilot.aedt_ops` —— 但 MVP 阶段这个独立工具暂不启用，
因为独立 server 连不上。MVP 走 run_python_code 路径，tools.py 保留但不阻塞。

### 新增 `docs/mvp-runbook.md`（演示运行手册）

记录：前置条件、8 步确切调用序列（含 run_python_code 的调用语句模板）、
预期产物清单。供演示时照着走 + 给领导看"每一步在干什么"。

## 验证标准（MVP 跑通的判据）

1. ✅ 步骤 5 导出的 s4p 包含 201 个频率点（非 1 个 LastAdaptive 点）
2. ✅ 步骤 6 的 metrics.json 中 n_freqs=201, freq_range=[0, 10GHz]
3. ✅ 全程零现场生成仿真代码（run_python_code 收到的都是固定 import 调用）
4. ✅ 步骤 8 后 license 归还（disconnect + 进程退出）
5. ✅ 产物不写回输入工程目录（输出在 runs/twinaxial-demo-001/ 下独立路径）

## 不做的事（MVP 范围外）

- 不做独立 em-copilot-mcp server 的活连接（已证伪，搁置）
- 不做 Job 模型/任务编排（Phase 2）
- 不做一键全流程命令（Phase 3）
- 不做验收指标评估（领导说工程师自代劳）
- 不改 states.py（已完整，Phase 2 用）
