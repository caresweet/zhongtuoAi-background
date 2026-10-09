# 复垦事实卡字段清单收口报告

**日期**：2026-10-09 · **基线**：`6d75b54` · **分支**：`feat/reclamation`

## 0. 结论一句话

事实卡原先只渲染一份**写死 59 键的稳评字段清单**（`build_project_card` 内联）—— 复垦自有字段即便有值也进不了卡、到不了模型眼前。本轮把**字段清单**也纳入域缝隙（`chapter_catalog.project_card_keys`），给复垦落了业主 2026-10-09 核准的字段清单与标注。**稳评逐字节不变**（stash-diff，3709 行 0 diff）。

---

## 1. 复垦事实卡字段清单（按实现，key → 卡片标注）

顺序即渲染顺序（`app/agent/reclamation_definitions.py` 的 `RECLAMATION_CARD_KEYS`）。

| 组 | key | 标注 |
|---|---|---|
| 项目 | `project_name` | 项目名称 |
| 项目 | `build_unit` | 建设单位（复垦义务人） |
| 项目 | `location` | 临时用地位置 |
| 项目 | `land_use` | 土地用途 |
| 项目 | `service_years` | 复垦服务年限 |
| 项目 | `doc_reference` | 批复/备案文号 |
| 勘测定界 | `area_m2` | 临时用地面积（平方米） |
| 勘测定界 | `area_mu` | 临时用地面积（亩） |
| 勘测定界 | `land_classification` | 地类构成 |
| 勘测定界 | `land_ownership` | 土地权属 |
| 勘测定界 | `boundary_desc` | 四至 |
| 勘测定界 | `plot_count` | 地块数 |
| 复垦方向 | `reclamation_direction` | 复垦方向 |
| 复垦方向 | `damage_type` | 损毁方式 |
| 复垦方向 | `damage_degree` | 损毁程度 |
| 复垦方向 | `suitability_grade` | 适宜性等别 |
| 复垦方向 | `topsoil_thickness` | 剥离厚度 |
| 复垦方向 | `reclamation_target` | 复垦目标 |
| 工程量与钱 | `earthwork_volume` | 工程量 |
| 工程量与钱 | `unit_price` | 综合单价 |
| 工程量与钱 | `construction_cost` | 工程施工费 |
| 工程量与钱 | `other_cost` | 其他费用 |
| 工程量与钱 | `dynamic_total_investment` | 动态总投资 |
| 图件与附件 | `map_plots` | 图斑 |
| 图件与附件 | `map_scale` | 比例尺 |
| 图件与附件 | `map_date` | 制图日期 |
| 图件与附件 | `rights_holder_opinion` | 权属人意见 |
| 日期 | `build_start_date` | 开工 |
| 日期 | `build_end_date` | 竣工 |
| 日期 | `reclaim_start_date` | 复垦开始 |
| 日期 | `reclaim_end_date` | 复垦结束 |
| 日期 | `acceptance_date` | 验收 |

共 32 键，与 `RECLAMATION_KEY_LABELS` 的键集**完全相同**（每个字段都有中文标注，不留裸 key）。

---

## 2. 业主 32 个拟议键的处置（kept / mapped / newly added）

### 2.1 直接沿用既有键（kept as-is，16 个）

| 业主拟议键 | 沿用键 | 依据（复垦取数管线里的既有出处） |
|---|---|---|
| `project_name` | 同名 | `RECLAMATION_KEY_LABELS`；可研解析器 `reclamation_fs_parser`；第1章 agent `required_data_keys` |
| `build_unit` | 同名 | `RECLAMATION_KEY_LABELS`；可研解析器；第1章 agent |
| `location` | 同名 | `RECLAMATION_KEY_LABELS`；可研解析器；勘测定界解析器 `land_survey_parser`；第1章 agent |
| `land_use` | 同名 | `RECLAMATION_KEY_LABELS`；勘测定界解析器（`land_use`） |
| `service_years` | 同名 | `RECLAMATION_KEY_LABELS`；第1章 agent |
| `land_classification` | 同名 | `RECLAMATION_KEY_LABELS`；`report.py` 依勘测定界写 `land_classification` |
| `land_ownership` | 同名 | `RECLAMATION_KEY_LABELS`；`report.py` 写 `land_ownership`（权属构成文本） |
| `reclamation_direction` | 同名 | `RECLAMATION_KEY_LABELS` |
| `dynamic_total_investment` | 同名 | `RECLAMATION_KEY_LABELS`；投资工具 `calc_investment` 输出项 |
| `boundary_desc` | 同名 | 事实卡既有键（`_PROJECT_CARD_KEYS`）+ 公告解析器 `announcement_extract` 写 `boundary_desc`。**本域首次启用**，故在 `RECLAMATION_KEY_LABELS` 新增其标注（`四至`） |
| `map_plots` | 同名 | 事实卡/公告解析器既有键。本域首次启用，新增标注（`图斑`） |
| `map_scale` | 同名 | 同上（`比例尺`） |
| `map_date` | 同名 | 同上（`制图日期`） |

> `boundary_desc`/`map_plots`/`map_scale`/`map_date` 四个：**键名**在事实卡/公告解析器里已存在（非新造），复垦此前不曾在卡上渲染它们；本轮纳入复垦清单并补上复垦口径的标注。

### 2.2 映射到既有键（mapped，1 个）

| 业主拟议键 | 映射到 | 理由 |
|---|---|---|
| `temp_land_area`（临时用地面积） | `area_m2`（+ `area_mu`） | 「临时用地面积」这个概念在复垦取数管线里**已有键**：勘测定界解析器 `land_survey_parser` 只出 `area_m2`，`report.py` 依它换算 `area_mu`（1 亩=666.67㎡）。**不另造同义词** `temp_land_area`。⚠️ **单位不入**：业主标注写的 `(hm²)` 未采纳 —— 管线只产 ㎡/亩，卡上按既有标注显示「临时用地面积（平方米）」与「（亩）」（详见 §5 待确认项 1）。 |

### 2.3 新增键（newly added，15 个）

| 业主拟议键 | 标注 | 理由（为何没有既有键可复用） |
|---|---|---|
| `plot_count` | 地块数 | 勘测定界解析器只暴露 `plots`（明细列表）+ `boundary_points`（界址点数），**无「地块数」标量键** |
| `damage_type` | 损毁方式 | 复垦方向评价字段，管线未建 |
| `damage_degree` | 损毁程度 | 同上 |
| `suitability_grade` | 适宜性等别 | 适宜性工具 `evaluate_suitability` 只返回计算结果（等别），**不写 filled_data 键** |
| `topsoil_thickness` | 剥离厚度 | 工程量工具 `calc_earthwork` 的入参名是 `topsoil_thickness_m`，**非数据集键** |
| `reclamation_target` | 复垦目标 | 管线未建 |
| `earthwork_volume` | 工程量 | 工程量工具输出为账本条目的中文名，**非数据集键** |
| `unit_price` | 综合单价 | 管线未建 |
| `construction_cost` | 工程施工费 | 投资工具入参名为 `engineering_cost`，**非数据集键** |
| `other_cost` | 其他费用 | 管线未建 |
| `rights_holder_opinion` | 权属人意见 | `reclamation.py` 的 `data_categories` 里是中文品类名「权属人意见」，**无英文字段键** |
| `build_start_date` / `build_end_date` | 开工 / 竣工 | 可研解析器出的是 `build_period`（起止时间文本），**非两个日期键**；此处按业主拆分 |
| `reclaim_start_date` / `reclaim_end_date` | 复垦开始 / 复垦结束 | 管线未建 |
| `acceptance_date` | 验收 | 管线未建 |

### 2.4 业主清单之外、复垦卡**保留**的 3 个既有键

`area_m2`、`area_mu`、`doc_reference` —— 这三个在旧实现下（走稳评 59 键清单）**已在复垦卡上渲染**：`doc_reference` 有既有测试断言（`test_reclamation_card_has_no_stability_wording` 要求卡上出现「批复/备案文号：…」），`area_m2`/`area_mu` 是临时用地面积的既有口径。**保留**以免回退；`doc_reference`（批复/备案文号）本就是复垦要引的文号。

---

## 3. 今日复垦会话事实卡渲染成什么样（如实）

**渲染为空（等价于稳评无数据时的中性兜底）**：

```
## 🔗 本项目事实卡（唯一事实源）
尚未掌握任何可确认的项目事实。你只能依据「可用数据」和「用户提供的全部资料」撰写，
所有项目具体数字一律用「以现状调查确认结果为准」式规范文字描述，严禁编造。
```

原因：**复垦的字段抽取管线尚未建成** —— 目前**没有任何代码**写 `RECLAMATION_CARD_KEYS` 里的键，`build_project_card` 按设计过滤「无值/待补充」字段，于是整块不出。本轮**只落「键 + 标注」契约**：不编造占位值、不接假数据源、绝不把标注打成没有值的一列空行（空卡 == 稳评空卡，逐字相同，见 `test_reclamation_card_empty_renders_neutral_fallback_not_labels`）。

有值时（例：`)`build_project_card({...}, "reclamation")`）渲染形如：

```
  项目名称：某某项目
  建设单位（复垦义务人）：某某公司
  临时用地位置：某镇某村
  复垦服务年限：3年
  复垦方向：耕地
  动态总投资：120.5万元
```

---

## 4. 哪些路径消费事实卡/lexicon —— 两条路都看得到复垦清单

事实卡由**一个**函数渲染：`master_orchestrator.build_project_card(filled_data, domain)`；字段清单经 `chapter_catalog.project_card_lexicon(domain)["keys"] → project_card_keys(domain)` 解析。所有路径都汇到它，故**全部**按域取清单：

| 消费点 | 文件:行 | 传域方式 |
|---|---|---|
| 路径 A：逐章直写 | `master_orchestrator.py` `build_chapter_prompt`（`:625`） | `build_project_card(filled_data, domain)` |
| 路径 B：照搬改写 | `reference_llm.py`（`:110`） | `build_project_card(filled, domain_of_state(state))` |
| 路径 B：缺口拟稿 | `ai_gap_writer.py`（`:111`） | `build_project_card(filled, domain_of_state(state))` |
| 工作流（两处） | `report_workflow.py`（`:936`、`:1577`） | `build_project_card(filled or {}, domain)` |

`project_card_lexicon` 本身**只**被 `build_project_card` 消费（全仓唯一调用点）。因此：**两条生成路径（A/B）都看得到复垦清单**，无遗漏、无第二份真相。`domain_of_state(state)` 对复垦会话返回 `"reclamation"`，路径 B 亦然。

---

## 5. 稳评逐字节验证（stash-diff，非目测）

探针脚本渲染 **5 组事实卡样例 + 稳评 14 章完整 `build_chapter_prompt`**（含全 59 键 / 空 / 部分字段 / `domain=None`），`git stash` 回 `6d75b54` 与改动后各跑一次、全文比对：

```
$ git stash && python3 _card_probe.py > /tmp/card_old.txt && git stash pop
$ python3 _card_probe.py > /tmp/card_new.txt && diff /tmp/card_old.txt /tmp/card_new.txt
RESULT: byte-identical (3709 lines compared)   # 0 diff
```

覆盖：稳评 59 键的**键集、顺序、标注、渲染文本** + 三附带小节（预公告/勘测定界/座谈会）+ 用户补充说明/兜底草稿 + 14 章 system/user 提示词。全部逐字节相同。（探针为临时文件，验完即删，未入库。）

---

## 6. 测试结果 vs 基线

| | passed | skipped | failed |
|---|---|---|---|
| 基线 `6d75b54` | 444 | 1 | 5 |
| 本轮后 | **449** | 1 | 5 |

+5 = 本轮新增用例（`tests/test_prompt_db_leaks_domain.py` 由 14 例增至 19 例）。5 个 failed 与基线**逐一相同**（`test_date_source_rule`、`test_hitl_resume_loop`×2、`test_survey_fixes`×2），系本分支既有，未追。

新增用例：
- 稳评字段清单缝隙 == `_PROJECT_CARD_KEYS`（且长度 59）；
- 复垦字段清单 == `RECLAMATION_CARD_KEYS`（复垦独有字段在、稳评专有字段不在、每键有标注）；
- lexicon 两域都暴露 `keys`；
- 复垦自有字段（有值）进卡且用复垦标注；
- 复垦空数据 → 中性兜底（== 稳评空卡），不是空标注列。

---

## 7. 未留死代码 / 未新增依赖

- 稳评清单由 `build_project_card` 内联**上提**为 `_PROJECT_CARD_KEYS`，缝隙经 `stability_project_card_keys()` 委托取用，**不复制第二份**；调用点无 `if domain == ...`（域差异全在 `chapter_catalog`）。
- `project_card_keys` / `stability_project_card_keys` / `RECLAMATION_CARD_KEYS` **均有被消费**，无写而不读。
- 无新依赖、未用 GIS/几何库（纯字符串装配）。

---

## 8. 自评（self-review）

- **§5 逐字节**：stash-diff 全文比对（3709 行 0 diff），非目测。
- **键集自检**：`RECLAMATION_CARD_KEYS` 32 键无重复、与 `RECLAMATION_KEY_LABELS` 键集相等（无裸 key）。
- **不留死代码**：旧内联清单已删；新增函数均有消费点。
- **测试不触网/不碰真实库**：新用例全为纯函数（`build_project_card`）调用，无 DB/网络。
- **待业主确认（2 项）**：
  1. **单位**：`temp_land_area` 业主标 `(hm²)`，实现按既有 `area_m2`/(亩) 渲染 —— 管线只产 ㎡/亩。若报告口径要用 hm²，需另定换算键或改标注（本轮未替做，未造同义词）。
  2. **3 处标注与业主拟议措辞不同，沿用既有 `RECLAMATION_KEY_LABELS` 值**：`land_use`→「土地用途」（业主写「临时用地用途」）；`build_unit`→「建设单位（复垦义务人）」（业主写「建设单位」）；`land_ownership`→「土地权属」（业主写「权属单位」）。因「映射到既有键」即沿用既有标注，且三者不改变既有行为；若要与业主措辞逐字对齐，改这 3 条 `RECLAMATION_KEY_LABELS` 值即可（影响面仅复垦）。
- **遗留（非本轮面）**：复垦取数管线未建 → 本轮后卡仍渲染为空；仅第 1–3 章有 agent（第 4–10 章路径 B 产出为空，既有）。
