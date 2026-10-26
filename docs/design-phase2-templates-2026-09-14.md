# Phase 2 设计报告：从零建模板 → 参数化改参 → 全链路演示

> **日期**：2026-09-14
> **基线**：em-copilot-core 0.1.dev0 + pyaedt 1.5.0 + AEDT 2025.2
> **配套文档**：[mvp-runbook.md](mvp-runbook.md)（11 步 demo）· [version-sync-2026-09-14.md](version-sync-2026-09-14.md)（架构与已完成功能）
> **一句话目标**：把"新建 `.aedt` 工程"这台戏搭起来，与已有的「建模→求解→导出→分析→精装报告」全链路拼成一场完整的演示。

---

## 1. 背景

Phase 1（MVP）已跑通"拉取既有工程 → 求解 → 导出 s4p → 分析 → 精装报告"共 11 步，
四件炫酷功能（截图、进度流、收敛曲线、精装 HTML）已全部落地。

但 Phase 1 的起点是**一个别人建好的 Twinaxial 工程**——观众只能看到"改参数 →
重算 → 看结果"，看不到"从零把模型造出来"这一步，说服力打折。

Phase 2 补上这块拼图，让演示从**空白画布**开始：

```
空 Project
   ↓ 模板函数从零建模
带几何的 .aedt 工程
   ↓ 填参 / 改参 / 校验
新版本参数工程
   ↓ 求解 + 进度流
结果数据
   ↓ 导出 + 分析 + 报告
精装 HTML 报告（截图 + 收敛图 + S参数图 + TDR 图）
```

---

## 2. 设计目标与原则

| 目标 | 说明 |
|------|------|
| 真实建模 | 模板用官方 API 从零生成真实三维几何，不是贴图 |
| 参数化 | 每个模板的尺寸/材料全部挂在设计变量上，可改可扫 |
| 可校验 | 建完模型先做几何/端口/边界自检，错了立刻报，不出脏结果 |
| 可复现 | 同一组参数 → 同一份工程，演示可反复跑 |
| 兼容老链路 | 输出仍走 Phase 1 已验证的「求解→导出→分析→报告」 |
| 安全（§14.1） | 每次建模都在独立子目录，绝不写原始工程 |

**架构纪律**（沿用 Phase 1）：所有模板函数固定写在本地函数库里，AI 只传
「模板名 + 参数 dict」，不写一行业务代码。参见 [version-sync-2026-09-14.md](version-sync-2026-09-14.md) 第 2 节。

---

## 3. 三个模板工程设计

选型标准：**覆盖最常见的传输线形态**，且互相不重复，参数化差异大，演示观感丰富。

### 3.1 T1 — 同轴线缆模板 `coax`

| 项 | 值 |
|----|----|
| 形态 | 同轴：内导体 + 介质 + 外导护套，两端配 Wave Port |
| 端口结构 | 2 个 Wave Port（内外导体环面），单模传输 |
| 分析目标 | 插损 S21、回损 S11、特征阻抗 vs 频率 |
| 可视化 | 圆柱截面清晰，截图最直观 |

**参数白名单**：

```
Ci_R_inner   内导体半径
Ci_R_outer   外导体半径
Ci_epsr      介质介电常数
Ci_losst     介质损耗角正切
Ci_len       线长
Ci_R_screen  屏敝/外护半径（可空 = 无护套）
```

**建模步骤（函数内固定实现）**：

```text
1. 新建 Project + HFSS design（Terminal 解）
2. set_variable 定义 Ci_* 参数
3. create_cylinder(内导体, Cu)          → 全高
4. create_cylinder(介质, 自建介电质)     → 全高
5. create_cylinder(外导体/护套, Cu)      → 全高
6. subtract 介质掏空内导体；外护掏空介质（保三方）
7. create_region 建立空气边界
8. wave_port(截面内-外环) x2            → Port1/Port2
9. assign_radiation_boundary(空气边界)
10. 网格：对内导体/介质加密
11. create_setup(频率、pass、DeltaS)
12. create_linear_count_sweep(0~20GHz)
13. validate_full_design()               → 自检
14. save_project(独立目录/.aedt)
```

**自检点**：内导体体积 > 0；内外半径差 > 0；介质区实体存在；两端端口面有解。

---

### 3.2 T2 — 微带线模板 `microstrip`

| 项 | 值 |
|----|----|
| 形态 | 地平面上方一条走线，谐振腔空气盒 + 辐射边界 |
| 端口结构 | 2 个 Wave Port（通常 Q3D/HFSS 巷口），传输线模式 |
| 分析目标 | S21 / S11、特性阻抗、弯曲/宽度敏感性 |
| 可视化 | 平板 + 走线俯视、侧视都清晰 |

**参数白名单**：

```
Mj_W        走线宽度
Mj_T        走线厚度
Mj_h        介质厚度（走线到底板）
Mj_epsr     基板介电常数
Mj_losst    基板损耗角正切
Mj_len      走线长度
Mj_gap      （可空）走线到空气盒边距
```

**建模步骤**：

```text
1. 新建 Project + HFSS design（Modal 或 Terminal）
2. 定义 Mj_* 参数
3. create_box(基板)                       → 介质
4. create_box(地平) 或 sheet 上盖           → GND（PEC）
5. create_box(走线, Cu, Mj_W×Mj_T×Mj_len) → 信号线
6. create_box(空气盒) 扩展 Mj_gap
7. wave_port(走线端面 - 底面) x2
8. 辐射边界(空气盒)
9. 走线表面网格加密
10. create_setup / sweep
11. validate_full_design()
12. save_project()
```

**自检点**：走线不穿透基板；走线两端落在空气盒面；端口参考面明确；地平连续。

---

### 3.3 T3 — 双绞差分线模板 `twinax`

> 与 Phase 1 使用的工程同族但**从零重建**，方便对照演示。

| 项 | 值 |
|----|----|
| 形态 | 两根导体对绞/平行，内置差分对，外套或共地 |
| 端口结构 | 2 个差分 Wave Port / Terminal，差分与共模同时解开 |
| 分析目标 | Sdd21 / Sdd11 / Scd21 模式转换 |
| 可视化 | 二维对绞剖面，模式转换图最抓眼球 |

**参数白名单**（沿用 Phase 1 命名，方便对接）：

```
Tx_R_sig     信号导体半径
Tx_R_drn     排流/回流导体半径
Tx_spread    两导体中心距
Tx_epsr      绝缘介电常数
Tx_losst     绝缘损耗角正切
Tx_len       线长
Tx_R_out     外护/共地（可空）
```

**建模步骤**：

```text
1. 新建 Project + HFSS design（Terminal）
2. 定义 Tx_* 参数
3. 两根 create_cylinder(信号, Cu)
4. （可选）第三根 create_cylinder(排流, Cu)
5. create_cylinder(绝缘介质包住两芯)
6. （可选）外护/共地
7. subtract 掏空
8. create_region + 辐射边界
9. 端口 x2（差分解，terminal 分正负）
10. 网格加密（两导体之间电介质细剖）
11. create_setup / sweep
12. validate_full_design()
13. save_project()
```

**自检点**：两芯不重叠；间距 > 0；绝缘包全两芯；端口正负端子都指到；共模不漏解。

---

### 三模板对比

| | T1 同轴 | T2 微带 | T3 双绞差分 |
|---|---|---|---|
| 传输线类型 | 轴对称 | 平面 | 对称差分 |
| 端口 | 2 Wave Port | 2 Wave Port | 2 差分 Terminal |
| 解类型 | 单模 | 单模 | 差分/共模 |
| 主要看头 | 截面截图 | 走线布局 | 模式转换图 |
| 复用 Phase1 | 全部 | 全部 | 全部 + Twinaxial 对比 |

**参数设计原则**：
- 每个模板参数 `dict` 带**类型标注 + 单位 + 上下限**（模板内校验，超限拒绝，不出脏模型）。
- 可选参数（如"可空 = 无护套"）模板内部判断，不硬编码成必须。
- 全部挂设计变量，后续可一键接 Optimistic 扫描。

---

## 4. 实施流程（全链路演示脚本）

分四条主流程，一条一条跑通，最终合成一个完整 demo。

### 4.0 预处理
```bash
# 清残留锁文件（若 PID 已退出）
# 检查 .venv、check_aedt_installed、MCP connected
```

### 4.1 流程 A：从零建模板（新能力）
```python
# 通过 run_python_code 调用固定模板函数
result = ops.build_template_project(
    desktop,
    template="coax",                    # 或 microstrip / twinax
    params={
        "Ci_R_inner": "0.5mm",
        "Ci_R_outer": "2mm",
        "Ci_epsr": "2.1",
        "Ci_losst": "0.002",
        "Ci_len": "50mm",
    },
    out_dir=r"D:\HCX\HFSS\em-copilot\runs\gen\coax_001",
)
# 返回 JSON: {success, project, design, validate:[...], error?}
```

**可见产物**：
- 工程文件（独立目录）
- `validate` 自检结果（体积>0、端口OK…逐条列给观众看）
- 可选：立即 `get_model_screenshot` 出 3D 图

### 4.2 流程 B：改参 + 校验（新能力）
```python
result = ops.apply_params_and_validate(
    desktop,
    template="coax",
    project=r"...\coax_001\CoaxDemos.aedt",
    params={"Ci_R_outer": "1.8mm", "Ci_epsr": "2.4"},
)
# 返回: {applied:{...}, validate:[...], changed:["Ci_R_outer","Ci_epsr"]}
```
- 逐个参数 set_variable → 校验 → 汇总变更清单。
- 校验失败该参数不落库，明确报错。

### 4.3 流程 C：求解 + 进度（已有，复用）
`analyze_setup` + Monitor 监控收敛，实时展示 Pass/预算。

### 4.4 流程 D：导出 + 分析 + 精装报告（已有，复用）
```python
# 导出 201 点 s4p → em_copilot 离线分析 → render_markdown(含截图/收敛图)
```
产出 Phase 1 已卡片化的 7 个文件：report.html / md、截图、收敛图、S参数图、TDR 图、metrics.json。

### 4.5 合成「一键 Demo」
把 A→B→C→D 串成一条命令（脚本级编排，仍是固定函数，AI 只传模板+参数）：
```text
coax   建模板→改参1组→求解→导出→分析→报告   (全流程)
microstrip 建模板→改参1组→求解→导出→分析→报告
twinax 建模板→改参1组→求解→导出→分析→报告  (可横向对比 Twinaxial)
```
每步输出均打明印，观众能看见「空白 → 建模 → 参数 → 求解 → 结果 → 报告」整个过程。

---

## 5. 新增代码与文件清单

### 新模块（建议 `src/em_copilot/templates.py`）
| 函数 | 职责 |
|------|------|
| `build_template_project(desktop, template, params, out_dir)` | 按模板从零建 `.aedt` |
| `apply_params_and_validate(desktop, project, params)` | 改参 + 校验 |
| `validate_built_model(design)` | 体积/端口/边界自检，返回逐条结果 |
| `_build_coax/_build_microstrip/_build_twinax` | 三个具体建模函数（内部） |
| `TEMPLATE_SCHEMAS` | 各模板参数白名单（含单位、范围、可选标记） |

### 配套
- `docs/demo-phase2-script.md`：可执行的分步 Demo 脚本（面向演示者/领导）
- 模板参数 schema 另存 JSON，便于校验与文档化

### 复用（不重写）
- `aedt_ops.get_model_screenshot`：截图
- `aedt_ops.export_touchstone_sweep`：导出
- `aedt_ops.get_convergence_data`：收敛
- `reporting.build_report_data / render_markdown`：报告

---

## 6. 风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| 从零建模几何不闭合、端口漏设 | 中 | 每个模板 `validate_built_model` 逐条自检，失败即报，不落到求解 |
| 建模参数越界（如内径>外径） | 中 | 模板 schema 带上下限，进入前校验拒绝 |
| gRPC 下部分 API 不可用 | 中 | 沿用 Phase 1 fallback 策略：优先文件解析、其次 profile API |
| 求解时间长 | 中 | 演示默认用小网格/少 pass 快速档；提供「完整档」供会后跑 |
| 出现与现有 Twinaxial 命名混淆 | 低 | 模板独立目录 + 命名前缀（gen/），文档写明归属 |

---

## 7. 里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 | T1 同轴模板 从零建模 + 自检通过 | 可跑的 `coax_001/.aedt` |
| M2 | T2 微带 + T3 双绞 建模 + 自检通过 | 三个模板样例 |
| M3 | 改参与校验闭环（B 流程） | `apply_params_and_validate` 可跑 |
| M4 | 合成一键 Demo（A→D 全链路） | `demo-phase2-script.md` + 录像 |
| M5 | 全链路联调 + 领导演示 | 一次跑通的演示 |

**验收标准**：
- 三个模板各能从空白建出 `.aedt` 且 `validate` 全绿。
- 改任一参数 → 重新校验 → 求解 → s4p 频率点数、收敛曲线、报告齐全。
- 全程 AI 未手写一行求解代码（只有模板名 + 参数 dict）。
- 每次运行产物在独立目录，原工程零污染。

---

## 8. 总结

Phase 2 把演示的起点从「捡现成工程」前移到「从零建模」，三个模板（同轴 / 微带
/ 双绞差分）覆盖三种主流传输线，全部由固定模板函数 + 参数 dict 生成。再叠加
Phase 1 已完成的求解、进度流、导出、分析、精装报告，构成「空白 → 成品报告」
的完整故事线，说服力和观赏性都明显更强，且严格保持「AI 只传参数、不写业务代码」
的架构纪律。
