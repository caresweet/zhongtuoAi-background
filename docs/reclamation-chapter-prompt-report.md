# 复垦章默认单章 system prompt 按域装配 —— 交付报告

**范围**：`app/agent/agents/chapters/chapter_base.py::_default_system_prompt`（约 `:1793`）。
这是 `ChapterOrchestrator` 路径（前端 `POST /api/v1/reports/generate/{sid}/chapter/generate`）
的**活 system prompt** —— 该路径不设 `_use_custom_prompt`，这条默认提示词就是模型实际收到的。

**收口点**：`app/agent/chapter_catalog.py`（本分支唯一缝隙）。新增缝隙函数，**没有**在
`chapter_base.py` 里写 `if domain == ...`。

**域标识来源**：新增类属性 `ChapterAgentBase.domain = "stability"`，`ReclamationChapterAgentBase`
覆盖为 `"reclamation"`。取类属性而非 `state`，是为了与同基类里已按类覆盖的
`chapter_title` / `rag_query_extra` 一致 —— 复垦 agent 的自身身份不依赖 state 是否恰好在场，
避免「标题已按复垦、提示词却回落稳评」的内部不自洽。

---

## 1. 提示词随域而变的部分（改动前 → 改动后）

| 片段 | 稳评（改动前＝改动后，逐字） | 复垦（改动后） |
|---|---|---|
| 身份首句 | `你是一位资深稳评工程师，长期做土地征收社会稳定风险评估。` | `RECLAMATION_IDENTITY_PROMPT`（域配置里本就有） |
| 核心原则首条 | `根据DB32/T4013-2021规范和你8年的实战经验来组织内容` | `依据《土地复垦条例》《土地复垦条例实施办法》与 TD/T 1031 系列编制规程，结合复垦方案编制经验组织内容` |
| 数据归位铁律块 | 6 行稳评版（见下 §3） | 11 行复垦版（见下 §3） |
| 基层过渡语示例 | `- 基层过渡语：「结合入户走访」「根据社区书记反馈」` | `- 基层过渡语：「结合现场查勘」「根据权属人意见」` |
| 标准小标题块 | `template_headings.format_headings_for_prompt(n)` | `""`（复垦的小节名与 `X.Y` 编号已由「本章结构规范」逐条给出，再出一份就是重复） |
| 章节结构规范块 | `chapter_definitions.format_sections_for_prompt(n)` | `chapter_catalog.sections_for_prompt` → 复垦十章目录（`X.Y` 小节） |
| 人工模板底稿块 | `template_chapter_chunker.build_template_block_for_prompt(n)`（读库，稳评模板） | `""`（公司模板库目前只有稳评模板，复垦无底稿） |
| 表格骨架 | `knowledge_agent.TEMPLATE_TABLE_SKELETONS[n]` | `{}`（该表是稳评报告模板里提取的，如第3章「拟征收土地基本情况表」） |

**原地保留、逐字不变的通用规则**（任务点名要求存活的）：AI 套词禁令、`第一/第二/第三` 禁令、
禁编造数据、`像真人工程师写报告`、`实施单位：{_impl_unit_hint}` 提示逻辑、
`范文数据一律作废` 块、`禁止模糊表达` 块、以及 `skeleton_rule` / `heading_constraint` /
`section_spec` / `template_block` 的**注入机制**（只是取值改为按域）。

---

## 2. 数据归位铁律 —— 复垦版全文

```
## 🔴 数据归位铁律（本报告各章数据各归其位，不得越界、不得重复）
- 第1章「前言」的复垦方案摘要只作概述：服务年限、各类土地面积、土地损毁情况、复垦目标、投资情况各以结论性数字点到为止，明细归各自专章，不得展开
- 项目基础信息（项目名称、建设单位/复垦义务人、临时用地位置、服务年限）在第3章「项目概况」集中交代，后续章节需要时以通称指代，不再重复罗列
- 土地利用现状与地类构成只在第3章「项目概况·项目区土地利用状况」与第4章「复垦区土地利用状况」出现，其他章不得重复罗列地类面积明细
- 土地损毁分析与评定、复垦适宜性评价、水土资源平衡、生态保护红线及生态空间管控分析、复垦的目标任务只属于第4章「土地复垦方向可行性分析」
- 复垦质量要求与预防控制、复垦（工程/生物化学/永久基本农田）、监测、管护各项措施只属于第5章「土地复垦质量要求与复垦措施」
- 工程设计及工程量测算数据只属于第6章「土地复垦工程设计及工程量测算」
- 投资估算数据（工程施工费、设备购置费、其他费用、监测管护费、预备费、动态总投资）只属于第7章「土地复垦投资估算」，其他章只能引用其结论数字，不得重复估算过程与费用明细
- 复垦工作计划与费用安排（分期、进度）只属于第8章「土地复垦工作计划安排」
- 效益数据（社会效益、生态效益、经济效益、复垦后耕地质量）只属于第9章「土地复垦效益分析」
- 文号（批复/备案文号）必须严格来自「可用数据」里的真实文号，绝不编造、不改号、不抄范文里的文号
- 同一数据全文只写一次，避免同一组数字在多章反复出现
```

### 每条规则的推导来源（全部来自 `app/agent/reclamation_definitions.py`）

| 规则 | 推导来源 |
|---|---|
| 第1章摘要只作概述 | ch1 `description`：…复垦方案摘要（服务年限、各类土地面积、土地损毁情况、复垦目标、投资情况…）；ch1 `sections`：`1.2 复垦方案摘要` |
| 项目基础信息归第3章 | ch3 `title`「项目概况」+ `description`「项目简介」+ `RECLAMATION_KEY_LABELS`（项目名称/建设单位（复垦义务人）/临时用地位置/复垦服务年限） |
| 土地利用现状/地类构成归第3、4章 | ch3 `3.4 项目区土地利用状况`；ch4 `4.2 复垦区土地利用状况` |
| 损毁分析/适宜性/水土平衡/生态红线/目标任务归第4章 | ch4 `title` + `sections` 4.1–4.7（与 `key_tables`「评价单元土地损毁情况表/评价单元复垦方向表」一致） |
| 质量要求与各项措施归第5章 | ch5 `title`「土地复垦质量要求与复垦措施」+ `sections` 5.1–5.5（与 `description` 的「预防控制/复垦/监测/管护」一致） |
| 工程量测算归第6章 | ch6 `title`「土地复垦工程设计及工程量测算」+ `key_tables`「工程量测算表」 |
| 投资估算归第7章 | ch7 `title`「土地复垦投资估算」+ `description`（工程施工费/设备购置费/其他费用/监测管护费/预备费、技术经济指标）+ `key_tables` |
| 计划与费用安排归第8章 | ch8 `title`「土地复垦工作计划安排」+ `sections` 8.2/8.3 + `key_tables`「复垦工作计划与投资安排表」 |
| 效益数据归第9章 | ch9 `title`「土地复垦效益分析」+ `sections` 9.1–9.4（含 9.4 复垦后耕地质量） |
| 文号不编造 | 复垦同样以批复/备案文号为准（`RECLAMATION_KEY_LABELS`: `doc_reference`「批复/备案文号」） |
| 同一数据只写一次 | 域内通用（稳评同款，保留） |

---

## 3. 因「无复垦对应物」而未带过来的规则

稳评那份 6 条里，有 3 条写死了稳评专有章义，复垦没有对应落点，**未翻译、直接丢弃**：

1. **「勘测定界数据…不得出现在第7章四性分析」** —— 「四性分析」（合法性/合理性/可行性/可控性）
   是稳评专有章义，复垦第7章是「投资估算」。故丢弃，改由复垦自己的 ch4 规则承接
   （损毁/适宜性只归第4章）。
2. **「风险因素与评分只属于第8章」** —— 复垦无风险评分概念，其第8章是「工作计划安排」。
   丢弃，不迁移。
3. **「问卷/座谈数据（份数、支持率、参会人数、诉求）只属于第6章」** —— 复垦章节目录里
   没有「支持率/参会人数/诉求」这类数据字段，复垦第6章是「工程设计及工程量测算」。
   丢弃，不迁移。（复垦第10章虽有 10.5 公众参与 / 10.6 土地权属调整方案，但它们是
   **程序性**内容、无对应数据集，故未增设「数据归位」规则。）

结果：复垦铁律 **11 条**，比稳评的 6 条更密（稳评把 4 类数据压进 4 条，复垦按 10 章逐章落位）。

---

## 4. 经缝隙收口 vs. 原地保留

**新增到 `chapter_catalog`（缝隙）**：
`chapter_persona` / `chapter_principle_lead` / `chapter_data_iron_rule`
（含 `_STABILITY_DATA_IRON_RULE`、`_RECLAMATION_DATA_IRON_RULE` 两个常量）/
`chapter_transition_examples` / `headings_for_prompt` / `template_block_for_prompt` / `table_skeletons`。
已有的 `sections_for_prompt` 直接复用。

**原地保留（`chapter_base.py`，因域中性/为稳评字节不变的锚点）**：禁止块、范文块、
模糊表达块、鼓励块（除过渡语那一行）、`_impl_unit_hint`、`AI_BUZZWORDS` 拼装、
`skeleton_rule` 文本、四处结构块的拼接机制。

**顺带清掉的死代码**（就在被重写的方法里）：`project_name` 与 `kb_ctx/kb_spec/
kb_constraints/sections_list/required_tables/min_w/max_w` 全是**只算不用** ——
`get_knowledge_context_for_chapter()` 在该方法里此前被调用但结果从未进入返回值。
已删除，方法输出不变。

**额外收口（`TEMPLATE_TABLE_SKELETONS` 的其余 3 处读取）**：`_default_user_prompt`、
`_build_table_skeleton_instruction`、`_build_kb_context_section` 也直接读该稳评全局。
任务把它列为「稳评结构注入点」之一（`TEMPLATE_TABLE_SKELETONS.get(self.chapter_number)`），
且复垦第3章会因此被注入「拟征收土地基本情况表」。三处一并改走
`chapter_catalog.table_skeletons(self.domain, n)` —— 保证缝隙是唯一取用点，
复垦不再拿到稳评表名。稳评分支逐字委托，用户提示词逐字节不变（见 §5）。

---

## 5. 稳评字节不变 —— stash-diff 验证

不靠肉眼，用确定性脚本把「结构类来源」打桩成哨兵后抓全文，`git stash` 前后各跑一次：

- `/private/tmp/verify_all.py`：抓 `_default_system_prompt`，**全 14 章**（各章 agent 逐个构造）。
  结果：`all_before.txt` vs `all_after.txt` **`diff` 为空 → ALL_STABILITY_BYTE_IDENTICAL`（538 行）。**
- `/private/tmp/verify_user.py`：抓 `_default_user_prompt` + `_build_table_skeleton_instruction`
  （第 1/3/5/9/12 章）。`diff` 为空 → **`USER_PROMPT_BYTE_IDENTICAL`。**
- `verify_prompt.py`（第8章单章）另有 `BYTE_IDENTICAL` 记录。

打桩项：`format_sections_for_prompt` / `format_headings_for_prompt` /
`build_template_block_for_prompt` / `TEMPLATE_TABLE_SKELETONS` —— 替换为带章号哨兵，
使快照既可复现又不读盘。

---

## 6. 测试

- 新增 `tests/test_chapter_prompt_domain.py`（13 项，全过）：锁定稳评字面量 +
  复垦身份/铁律逐条断言 + 复垦三章提示词不含 `稳评/DB32/社会稳定风险评估/拟征收土地`。
  不触网、不碰真实库（`build_template_block_for_prompt` 打桩；复垦分支本就不读库）。
- **全量**：`5 failed, 404 passed, 1 skipped`。
- **基线**：`5 failed, 391 passed, 1 skipped`（相差 +13 = 新增用例）。
  失败 5 项与基线同一组（survey / date / HITL），未追。

---

## 7. 自查发现（Self-review）

1. **（重要，越界未修）用户提示词路径仍有稳评物料泄漏。** `_default_user_prompt` 会经
   `app/agent/template_content.py` 注入 `data/template_chapters.json`（稳评模板）里的
   「写作标杆」整段；`_default_user_prompt` 里还写死一行
   `### 材料1：DB32/T4013-2021规范 + 优秀范文`。实测复垦第2/3章的 user prompt 里出现
   稳评公司简介与整串稳评法规清单。**这是 persona 之外的「物料」类泄漏**，与本次任务
   （persona/结构）不同类，且修它要动 `template_content.py`（独立模块 + 稳评专有 JSON），
   故**未修**，留作后续。若目标是「复垦正文整体不带稳评味」，这条必须另开一轮。
   （另：`_build_seed_context()` 读稳评 seed markdown，但其返回值 `seed_ctx` 未被使用，
   是既有死代码。）
2. `_build_kb_context_section` 是**既有死代码**（全仓仅一处定义、无人调用）。按范围未删，
   只把它的骨架读取改走缝隙，避免「缝隙唯一取用点」出现第二份。
3. 鼓励块里 `结论适度模糊`、`缺数据写「以现状调查确认结果为准」` 等**逐字保留** ——
   属域中性行文建议。
4. 复垦第1章结构块与稳评第1章不同：稳评 `format_sections_for_prompt(1)` 特判返回空串
   （第1章体例由「第一章特定要求」单独给），复垦无该特判、`_reclamation_sections_for_prompt(1)`
   照常给出 `1.1/1.2`。属复垦自身口径，非缺陷。
