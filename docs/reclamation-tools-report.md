# 复垦专属工具（工程量测算 / 投资估算 / 适宜性评价）交付报告

- 仓库：`/Users/mac/WorkBuddy/zhongtuo-report/backend`，分支 `feat/reclamation`，BASE `6971587`
- 单章工具回合的**域收口点**：`app/agent/chapter_catalog.py`；工具声明/执行：`app/agent/tools/registry.py`
- 新增：`app/agent/tools/reclamation_tools.py`（三件计算工具 + 规则表）
- 数据层成为「有调用者」：`app/services/reclamation_data_model.py` 的 `ReclamationDataModel` 首次被真实调用（三件工具都建账本、跑 `audit()`）

---

## 0. 结论速览

复垦章此前**整轮跳过**工具回合（`tool_round_schema("reclamation") == []`）。本次给复垦配了它自己的工具表：

| 工具 | 归属 | 说明 |
|---|---|---|
| `retrieve_rag` | 复用（稳评+复垦） | 检索本身按域隔离；**描述按域取**——复垦不再被点名 DB32/T4013 |
| `parse_kd_table` | 复用（稳评+复垦） | 复垦调用时走 `RECLAMATION_PROFILE`（临时用地表型） |
| `calc_earthwork` | **新增·仅复垦** | 工程量测算 |
| `calc_investment` | **新增·仅复垦** | 投资估算 |
| `evaluate_suitability` | **新增·仅复垦** | 适宜性评价 |

- 稳评工具表＝原五件，**逐字不变**（见 §4 stash-diff）。
- 复垦工具表＝`[retrieve_rag, parse_kd_table, calc_earthwork, calc_investment, evaluate_suitability]`；稳评专有器物（扣分制评分 / 公众·部门问卷）**不进表**。
- 每个计算结果都是 `TracedValue`（值 + 来源 + 算法），工具返回串里**连 `audit()` 一起给出**。

---

## 1. 逐工具说明

### 1.1 `retrieve_rag`（复用，描述按域）

- **参数**：`query`（必填）、`n_results`（1–8）。
- **返回**：`【知识库检索结果】…` 文本片段。
- **改动**：加 `domains=("stability","reclamation")`；`description_by_domain["reclamation"]` 把
  「稳评规范（DB32/T4013-2021）」换成「复垦编制规范（TD/T 1031 系列编制规程）」。
  稳评那份 `description` **一字未动**。检索口径早已按域（`retrieve_hybrid(domain=ctx["domain"])`），功能无需改。
- **样本依据**：无（检索能力本身）；描述口径依据 `app/domains/reclamation.py` 的 `RECLAMATION_IDENTITY_PROMPT`（《土地复垦条例》/TD/T 1031）。

### 1.2 `parse_kd_table`（复用，profile 按域）

- **参数**：`pdf_path`（可省）。
- **返回**：`format_for_prompt(parsed)`——地块汇总 + 土地分类面积。
- **改动**：加 `domains=("stability","reclamation")`；handler 内 `profile = RECLAMATION_PROFILE if ctx.get("domain")=="reclamation" else DEFAULT_PROFILE`。
  稳评走 `DEFAULT_PROFILE`（＝改动前 `parse_land_survey(target)` 的默认值），行为不变。
- **样本依据**：`app/services/land_survey_parser.py` 已带 `RECLAMATION_PROFILE`（临时用地，此前已加）。

### 1.3 `calc_earthwork`（新增·仅复垦）

- **参数**（面积 ㎡、厚度 m）：`soil_area_m2`／`topsoil_thickness_m`·缺省 0.3、`functional_area_m2`／`concrete_thickness_m`·缺省 0.10、`cushion_thickness_m`·缺省 0.08、`ditch_area_m2`／`ditch_coef`·缺省 1.5。
- **返回**：每项工程量的 `{名称, 值, 单位(m³ 与 百m³), 来源, 算法}` + `审计`。
- **算法**（业主批注即此）：
  - 表土剥离量 = 耕地面积 × 剥离厚度 → 井神 `1936×0.3 = 580.8 m³`（=5.808 百m³）
  - 混凝土层工程量 = 功能区面积 × 0.10 → `2354×0.10 = 235.4 m³`
  - 垫层工程量 = 功能区面积 × 0.08 → `2354×0.08 = 188.32 m³`
  - 沟渠疏通量 = 沟渠面积 × 1.5 → `393×1.5 = 589.5 m³`
- **依据样本**：井神（`6.1.1 工程技术措施设计` / `6.2 工程量测算`，表6-1）。四式与系数**逐字取自该样本**。
- **参数化说明**：0.3／0.10／0.08／1.5 是**该样本的工程系数**，故建成**可覆盖的缺省**（来源里注明「缺省取样本经验值」）；面积一律为入参。

### 1.4 `calc_investment`（新增·仅复垦）

- **参数**：`engineering_cost`（工程施工费，万元）或 `direct_costs`（各工程类别直接工程费，万元，→按 表7-1/7-2 合成工程施工费）；`equipment_cost`（设备购置费，缺省 0）；`acceptance_fee`（复垦验收费，据实，缺省 0）；`monitoring_persons`/`monitoring_months`（缺省 1/6）；`annual_static_investment`（各年静态投资，用于价差预备费）。
- **返回**：逐项 `{名称, 值(万元), 来源, 算法}` + `审计`，并附 `静态总投资`/`动态总投资`。
- **链条**：工程施工费 → 其他费用（前期工作费/工程监理费/拆迁补偿费/竣工验收费/业主管理费）→ 监测与管护费 → 预备费（基本预备费/风险金/价差预备费）→ 静态总投资 →（+价差预备费）→ 动态总投资。
- **对样本复算（井神）**：给 `工程施工费 6.79`、`复垦验收费 10.0`（4.2+4.8+1）、管护 1人×6月 →
  其他费用 `1.0975`（样本 1.10）、监测与管护费 `10.702`（样本 10.70）、静态总投资 `19.84`（样本 19.85）——**逐项吻合**。
- **依据样本**：井神（第7章）、王元佳苑、明远（三者交叉一致）。

### 1.5 `evaluate_suitability`（新增·仅复垦）

- **参数**：`factor_scores`（评价单元各三级因子分值 0–100，表10 口径，必填）、`unit_name`、`weights`（可覆盖缺省权重表）。
- **返回**：各因子加权贡献 + `加权总分` + `等别`（一等地≥80 / 二等地≥60 / 三等地<60）+ `审计`。
- **依据样本**：井神、污水处理厂（两者的「三级影响因子表」一致）。
- **⚠️ 分级标准未实现**：见 §3（因子**分值**直接入参）。

---

## 2. 每个费率/权重/标准：抽到还是留作入参

| 项 | 处理 | 来源 |
|---|---|---|
| 表7-1 措施费取费费率表（临时设施费/冬雨季/施工辅助/安全施工，七类工程） | **抽出→缺省**（`MEASURE_FEE_RATES`） | 井神/王元佳苑/明远/污水处理厂/宁淮城际 **5/5 逐字一致** |
| 表7-2 间接费取费费率表（七类工程） | **抽出→缺省**（`INDIRECT_FEE_RATES`） | 同上 **5/5 一致** |
| 计划利润 3%、税金 9% | **抽出→缺省** | 井神 §7.1.2 第3)4)条 |
| 其他费用各项费率（表7-4，14 项 + 业主管理费） | **抽出→缺省**（`OTHER_FEE_RULES`；单元格**直接写着算式**） | 井神/王元佳苑/明远 **3/3 逐字一致** |
| 复垦监测费 1.5% | **抽出→缺省** | 井神 §6.1.3/§7.2.4；王元佳苑/明远 表7-5 `1.5%[一]` |
| 基本预备费 6%（基数＝一+二+三+四） | **抽出→缺省** | 王元佳苑/明远 表7-5 `6%[一+二+三+四]`（井神 §7.1.2 正文基数写法与 §7.2.5 不一致，取表7-5 口径，见 §5） |
| 风险金 2%（基数＝工程施工费） | **抽出→缺省** | 表7-5 `2%[一]` |
| 价差预备费率 5% | **抽出→缺省** | 井神 §7.1.2 第2)条「年度价格上涨水平 5.0%」 |
| 管护费单价 1000 元/(人·月) | **抽出→缺省** | 井神 §7.2.4；王元佳苑 表7-5 |
| **价差预备费的年际口径（Wi 公式的 ni/年度金额）** | **留作入参** `annual_static_investment` | 样本无年度明细，无法反推（井神 2.03/19.85、王元佳苑 0.0801/1.1889、明远 0.72/20.31 各不相同，不能由静态投资单值复现） |
| **复垦验收费** | **留作入参** `acceptance_fee`（缺省 0） | 井神是**据实金额**（4.2/4.8/1 万），非费率；王元佳苑/明远该桶不含此项 |
| 适宜性**因子权重**（10 项三级因子，和 = 1.00） | **抽出→缺省**（`SUITABILITY_WEIGHTS`，可经 `weights` 覆盖） | 井神/污水处理厂「三级影响因子表」**2/2 一致** |
| 等别阈值（一等地 80–100 / 二等地 60–80 / 三等地〈60） | **抽出→缺省**（`GRADE_THRESHOLDS`） | 井神/污水处理厂「表9 备注」一致 |
| **适宜性分级标准（因素特征→分值）** | **未实现**（分值直接入参） | 见 §3 |
| 工程量系数 0.3 / 0.10 / 0.08 / 1.5 | **抽出→缺省**（可覆盖） | 井神 §6.1.1/6.2 |

---

## 3. 未能可靠抽取、留作入参 / 未实现的标准（Phase-2 · 交业主定夺）

1. **适宜性「分级标准」列（因素特征→分值）——未实现。**
   井神/污水处理厂的「三级影响因子表」在文本抽取里存在**噪声**：个别行分值**空白**
   （如「有效土层厚度 <5cm」「土壤质地 砂砾土、砂质土」无分值）；有机质区间出现
   `0.7,-.().8` 这类串字；自然坡度四行呈 `<30 / 30~70 / 7°~15° / >15°`（疑似应为
   `<7° / 7°~15° …`）。**照抄会把串字值写进报审文件**，故**不实现**「特征→分值」推导；
   `evaluate_suitability` 只吃**分值**（表10 口径），由调用方/后续 pass 供给。
   **这是业主/二期决定项。**

2. **5 份样本其实用了两套适宜性方法**：
   - **加权法**（本工具实现）：井神、污水处理厂——三级因子 + 权重 + 分值 + 等别阈值。
   - **限制因素法**：王元佳苑、明远、宁淮城际——「限制因素及分级指标表」（土壤侵蚀/坡度/覆土厚度/土壤有机质/土壤质地）→ 耕地/林地/草地等级 `1/2/3/N`，另附宜耕地/宜林地/宜牧地的一二三等地性状描述。
   本工具按**任务书描述**实现加权法；限制因素法**未实现**，报告为发现。

3. **`表10`（评价单元因子分值）与权重不自洽**：井神 表10 各列之和 = 95.50，
   但按「因子原始分值 × 表9 三级权重」回算，后几列对不上（如 排水保证程度 9.50 ÷ 0.09 = 105.6 > 100）。
   即**表9 的权重 × 表10 的分值 ≠ 表10 列值**。可能是表10 列另有口径或样本自身笔误——
   **无噪声证据，故不据此改权重**；工具按「分值 × 文档权重」求加权和，权重可经 `weights` 覆盖。

4. **价差预备费的年际口径**（见 §2）——需 `annual_static_investment` 入参。

5. **复垦验收费**是据实金额而非费率（见 §2）——入参。

---

## 4. 稳评行为验证（stash-diff，非目视）

- 用 `git stash push` 把 5 个被改的**已跟踪**文件回退到 `6971587`，在**同一脚本**下分别捕捉：
  `ALL_TOOLS_SCHEMA`、`_build_tool_decision_prompt` 的第 1/3/4/6/8 章输出、`_maybe_run_tool_round` 实际发给 LLM 的 `system`。
- `diff /tmp/stab_old.json /tmp/stab_new.json` → **无差异（BYTE-IDENTICAL）**。
- 即：**稳评工具表、逐章工具引导、工具回合身份串三者逐字不变**。

---

## 5. 数据层如何被使用 / 它已有了真实调用者

- `ReclamationDataModel` 此前**零调用者**。现 `calc_earthwork`/`calc_investment`/`evaluate_suitability` 三件工具**都**：
  1. `set_input` 登记直接量（带 `source`）；
  2. `derive(name, formula)` 登记派生量（带 `algorithm`）；
  3. 序列化账本时**一并返回 `audit()`**（返回串的 `审计` 字段）。
- 为此给数据层加了一个只读访问器 `items()`（`app/services/reclamation_data_model.py`，按登记顺序返回 `[(量名, TracedValue)]`），供工具导出整本账本——**最小改动、无死代码**。
- **引擎约束**：数据层 `_eval` 只支持四则 + 括号（`_OPS` 无 `**`）。故价差预备费的复利因子在 Python 里先算成字面量再写入算式（见 `reclamation_tools._calc_investment`），**未**给引擎加 `**`。

---

## 6. 测试

- 新增 `tests/test_reclamation_tools.py`（16 例）：工具归属/过滤、稳评工具表不变、`retrieve_rag` 描述按域、
  `parse_kd_table` profile 按域、三件工具对样本数值复算 + TracedValue + `audit()` 干净、缺参兜底、权重覆盖、缺因子提示。
- 更新 `tests/test_prompt_db_leaks_domain.py`：把「复垦工具表为空/整轮跳过」两条改为「复垦有自己工具表 + 决策调用用复垦身份」。
- **全量**：`python3 -m pytest tests/ -q` → **444 passed / 1 skipped / 5 failed**。
  基线 `428 passed / 1 skipped / 5 failed`。**失败集合完全一致（5 个均为本分支前既有）**，新增 16 例全绿。
- 测试不触网、不碰真实库（`execute_tool_calls` 直接调 handler；`parse_kd_table` 用 monkeypatch 拦 `parse_land_survey`）。

---

## 7. 自查发现（Self-review）

1. `**` 运算符：初版价差预备费算式写了 `((1+i) ** k - 1)`，数据层引擎**不支持 `**`**（会抛「算式里有不支持的写法」）。已改为 Python 预算因子再写字面量。
2. `_compute_other_fees` 分组小计顺序：业主管理费的基数含前几组小计，初版在循环后登记分组小计→会 `KeyError`。已改为「离开一组即登记」，并特判「工程监理费」组名与其唯一子项同名（不覆盖派生量）。
3. 井神 §7.1.2（正文）与 §7.2.5（成果）对**基本预备费基数**的写法不一致——取 表7-5 的 `[一+二+三+四]`（含监测管护费），因那是实际结果表的标注；已在报告标注。
4. 「安装工程」间接费基数是**人工费**（其余为直接工程费）：`direct_costs` 合成模式下无人工费，按直接工程费**近似**并在函数 docstring 注明——需精确时请直接给 `engineering_cost`。
5. 死代码清理：删除初版遗留的占位常量 `RECLAMATION_TOOLS_SCHEMA`、未被引用的 `INDIRECT_LABOR_BASE`、未使用的 `Optional` 导入。
6. 未重启 :8000 上的后端（按约定）。

---

## 8. 涉及文件

- `app/agent/tools/registry.py`（`ToolSpec.domains`/`description_by_domain`、`get_tools_schema(domain)`、五件工具按域标注、`parse_kd_table` profile 分支）
- `app/agent/tools/reclamation_tools.py`（**新**：三件工具 + 规则表）
- `app/agent/tools/__init__.py`（导入即注册复垦工具）
- `app/agent/chapter_catalog.py`（`tool_round_schema` 复垦分支 + 决策 prompt 的 `tool_round_decision_system` / `tool_round_available_tools`）
- `app/agent/agents/chapters/chapter_base.py`（工具回合改用上述 seam 函数）
- `app/services/reclamation_data_model.py`（加只读 `items()`）
- `tests/test_reclamation_tools.py`（**新**）、`tests/test_prompt_db_leaks_domain.py`（更新）
