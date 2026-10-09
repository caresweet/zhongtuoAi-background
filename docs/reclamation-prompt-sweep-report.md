# 复垦单章提示词稳定性泄漏 — 收口与全域清点

BASE: `97357e5` ｜ 分支: `feat/reclamation` ｜ 工作目录: `/Users/mac/WorkBuddy/zhongtuo-report/backend`

本报告分两部分：**Part 1** 关掉最后一处已知泄漏（`chapter_base._default_user_prompt`
的「材料1」抬头 + 稳评模板段落），**Part 2** 是对「稳评内容能从哪些入口进入复垦会话的
生成提示词/生成产物」的**全域清点**，逐项给出「现在读的是什么域的东西 / 对不对 / 结论」。

---

## Part 1 — 修复

### 1.1 缺陷

`app/agent/agents/chapters/chapter_base.py` `_default_user_prompt` 里，user prompt 的
「材料块」仍然把**稳评**素材喂给复垦章：

- 硬编码「材料1」抬头：
  `### 材料1：DB32/T4013-2021规范 + 优秀范文（最重要，优先参考）` —— DB32/T4013-2021
  是**稳评**的规范；把它作为复垦章「优先参考」的规范正是要消除的掺兑。
- 注入 `template_content.TemplateContentProvider.build_template_prompt(...)` —— 从
  `data/template_chapters.json`（**稳评产物**，12 条、按稳评章号建键）里挑一段模板段落
  当作「写作标杆」注入。
- 该处代码是**两段重复块**：第一段（`:1940`）的结果被 `:1950` 的 `template_content = ""`
  直接覆盖，**第一段是死代码**；夹在中间还有一行 `seed_ctx = self._build_seed_context()`
  同样**只写不读**（`_build_seed_context` 全仓无人调用）。

### 1.2 改法（收口到 `app/agent/chapter_catalog.py`，不在调用点写 `if domain == ...`）

新增四个缝隙函数（`chapter_catalog.py`）：

| 缝隙函数 | 稳评（默认域） | 复垦 |
|---|---|---|
| `material_heading(domain)` | 逐字 = `### 材料1：DB32/T4013-2021规范 + 优秀范文（最重要，优先参考）\n` | `### 材料1：复垦编制规范（《土地复垦条例》《土地复垦条例实施办法》与 TD/T 1031 系列编制规程）+ 优秀范文（最重要，优先参考）\n` |
| `template_reference_for_prompt(domain, ch, filled_data)` | 委托 `template_content_provider.build_template_prompt`（逐字不变） | **返回空串**（复垦无对标模板库，素材来自其自有 RAG `kb_reclamation_*`） |
| `custom_prompt_persona(domain)` | 逐字 = `你是一位资深稳评工程师。` | 复垦域 `identity_prompt` |
| `writing_guide(domain)` | 逐字 = 原内联文本 | 复垦身份 + 复垦小节示例 |

`chapter_base.py` 相应改动：

- `_default_user_prompt`：两段重复块 + `seed_ctx` 死行 → 一处
  `chapter_catalog.template_reference_for_prompt(domain, ...)`；「材料1」抬头 →
  `chapter_catalog.material_heading(domain)`；`domain = self.domain` 提到方法头，
  删掉重复的内层 `import chapter_catalog`。
- `act()`（两条 prompt 分支的**公共下游**，也在 user/system prompt 里）：
  - 自定义 prompt 分支兜底身份 `system_prompt = "你是一位资深稳评工程师。"` → `custom_prompt_persona(self.domain)`；
  - 标准小标题约束 `format_headings_for_prompt(self.chapter_number)`（**稳评**子标题）→ `chapter_catalog.headings_for_prompt(self.domain, ...)`（复垦为 `""`）；
  - 写作引导块 `writing_guide`（内联，含稳评身份 + 稳评小节示例）→ `chapter_catalog.writing_guide(self.domain)`。
- 删除 `_build_seed_context`（读过 `seed_data/*.md` 稳评种子；其唯一调用点被本次改动移除后彻底无人调用 —— 不留死代码）。

### 1.3 为什么模板段落是**省略**而不是替换

`data/template_chapters.json` 是稳评产物（12 条，键为稳评章号）。复垦**没有**对标模板库，
其参考素材正确来源是**它自己的**域内 RAG 集合（`kb_reclamation_*`，由 5 份真实方案灌入）。
给复垦补一段「编造的模板段落」只会再造一次掺兑，故对复垦**省略**该块（返回空串），
并让 `material_heading` 如实点名复垦编制依据。

### 1.4 before / after（复垦 user prompt 的材料1）

```
# before（复垦章也在读）
### 材料1：DB32/T4013-2021规范 + 优秀范文（最重要，优先参考）
EXCTX...
## 📋 写作标杆 — 模板报告本章写法示例
### 模板段落示例（学习其风格、深度和结构）
```  <从 template_chapters.json 挑出的稳评模板段落> ...
```
# after（复垦）
### 材料1：复垦编制规范（《土地复垦条例》《土地复垦条例实施办法》与 TD/T 1031 系列编制规程）+ 优秀范文（最重要，优先参考）
EXCTX...
<无模板段落块>
```

稳评侧：**逐字不变**（见 §3）。

---

## Part 2 — 全域清点（表为主交付）

两条生成入口：

- **路径 A**：`ChapterOrchestrator` → `KnowledgeAgent` → `chapter_base.think/act` →
  `_build_llm_prompt` → `_default_system_prompt` + `_default_user_prompt`（前端「生成某章」
  走的这条，**不设** `_use_custom_prompt`；复垦章 1–3 走这条）。
- **路径 B**：`report_workflow` → `master_orchestrator.build_chapter_prompt` →
  `chapter_base.act()` 的 `_use_custom_prompt` 分支（复垦章 1–3 经 `report_workflow`
  可达，`report_workflow.py:1277` 置 `_use_custom_prompt=True`）。

复垦当前只实现第 1、2、3 章 agent（`agents/chapters/__init__.py::_reclamation_agent`，
其余章号返回 `None`），故清点以章号 ∈ {1,2,3} 为准。

### 2.1 清点表

| # | 提示词输入 | 读的是什么（现） | 域对不对 | 结论 |
|---|---|---|---|---|
| 1 | `chapter_persona`（system 身份） | `catalog.chapter_persona(domain)` | 对 | 已正确（上轮） |
| 2 | 核心原则首条规范 | `catalog.chapter_principle_lead(domain)` | 对 | 已正确（上轮） |
| 3 | 数据归位铁律块 | `catalog.chapter_data_iron_rule(domain)` | 对 | 已正确（上轮） |
| 4 | 基层过渡语示例 | `catalog.chapter_transition_examples(domain)` | 对 | 已正确（上轮） |
| 5 | 标准子标题（system） | `catalog.headings_for_prompt(domain,ch)` → 复垦空 | 对 | 已正确（上轮） |
| 6 | 本章结构规范（system） | `catalog.sections_for_prompt(domain,ch)` → 复垦 RECLAMATION 结构 | 对 | 已正确（上轮） |
| 7 | 人工模板底稿（system） | `catalog.template_block_for_prompt` → 复垦空 | 对 | 已正确（上轮） |
| 8 | 模板表格骨架（system + user 两处 table_enforcement + act table_instruction） | `catalog.table_skeletons` → 复垦 `{}` | 对 | 已正确（上轮） |
| 9 | **材料1 抬头** | 原写死 `DB32/T4013-2021` | **否** | **本次修复** |
| 10 | **模板报告写法示例块** | 原 `template_content_provider`（稳评 `template_chapters.json`） | **否** | **本次修复**（复垦省略） |
| 11 | RAG：`chapter_context`/`example_context`/`local_regulation_context` | `rag_service.retrieve_for_chapter(domain=state._domain)`，`KnowledgeAgent._rag_retrieve` 同 | 对（`_domain` 已在 `report.py:769/885` 写入会话 state） | 已正确 |
| 12 | `dynamic_examples = get_cached_few_shot(ch)` | `few_shot_service._few_shot_cache`（由 `refresh_few_shot_cache` 灌，**domain 默认 "stability"**，`report_workflow.py:1114` 未传域）；缓存键只有章号 | **否** | **仍稳评味，上报**（见 §2.2-①） |
| 13 | `few_shot = get_few_shot_with_skills(ch)`（act，system） | 读 `knowledge_base.db.review_skills`（稳评专家/用户 skill） | **否** | **仍稳评味，上报**（§2.2-②） |
| 14 | `get_few_shot`（report_styles，act 回退） | `report_styles.get_few_shot` 恒返回 `""`（已停用） | 对（空） | 已正确 |
| 15 | `_get_cached_learning_hints(ch)`（user） | `learning_service.build_learning_hints("stability")`（**硬编码 stability**）+ `ALL_CHAPTERS`(稳评14章) + `_is_chapter_relevant`(稳评章号) | **否** | **仍稳评味，上报**（§2.2-③） |
| 16 | `_get_expert_skill_hints(ch)`（act，user） | `master_orchestrator`，读 `knowledge_base.db.review_skills`（稳评） | **否** | **仍稳评味，上报**（§2.2-④） |
| 17 | 标准子标题约束（act，user） | 原 `format_headings_for_prompt`（稳评） | **否** | **本次修复**（→ `catalog.headings_for_prompt`，复垦空） |
| 18 | 写作引导块 `writing_guide`（act，system） | 原内联：稳评身份 + 稳评小节示例 | **否** | **本次修复** |
| 19 | 自定义 prompt 兜底身份（act，system） | 原写死「你是一位资深稳评工程师。」 | **否** | **本次修复**（→ `catalog.custom_prompt_persona`） |
| 20 | 真实数据约束块（act，user） | 项目自身 `filled_data`（域名/文号）；措辞含「征地文号」「社区/街道」；风险等级仅 5/6/8/9 | 措辞偏稳评 | **轻微，上报**（§2.2-⑤） |
| 21 | `FANWEN_STYLE_GUIDE`（act，user） | `chapter_definitions` 通用文风铁律（去 AI 腔）；范文来自本项目 RAG | 通用 | 可保留（建议上报知会） |
| 22 | `_format_rag_for_prompt` 的 `four_dimension_context` | `kb_four_dimension`（稳评四性案卷）；**仅 ch4/10 触发** | 复垦仅章 1–3 → 当前不可达 | **潜在，上报**（§2.2-⑥） |
| 23 | 工具预检索回合 system（act） | 写死「你是一个稳评报告助手。」；工具表 `parse_kd_table`/`query_survey`/`calc_risk_scoring`（稳评） | **否** | **仍稳评味，上报**（§2.2-⑦） |
| 24 | `_collect_user_data` | 从**项目自身**资料抽取（正则是稳评字段词，但取的是本项目数据） | 基本对 | 已正确（措辞轻微，知会） |
| 25 | `build_project_card`（路径 B） | `master_orchestrator._PROJECT_CARD_LABELS`（**稳评标注**：`稳评实施单位`/`稳评责任单位`/`征地预公告文号`/`征收面积`…） | **否** | **仍稳评味，上报**（§2.2-⑧） |
| 26 | `scoring_guidance`（路径 B） | `catalog.has_scoring_guidance` → 复垦 False | 对 | 已正确（上轮） |
| 27 | 本章特定要求 / 学习提示 / 反模式 / 旧项目块 / 法规白名单（路径 B else 分支） | `_catalog.requirements_block(domain,…)` 复垦非空 → **整段 else 不进** | 对 | 已正确（上轮） |
| 28 | `key_labels`/`report_persona`/`target_words`/`table_format_reference`（路径 B） | 均经 `chapter_catalog(domain)` | 对 | 已正确（上轮） |

### 2.2 其它稳定性校准数据文件的可达性

- **`data/template_chapters.json` / `template_content.py`**：唯一读取点在
  `chapter_base._default_user_prompt`（经 `chapter_catalog.template_reference_for_prompt`）。
  复垦分支返回 `""` 且**不调用**提供器 → 复垦提示词**不再读**该稳评产物。✅（本次修复）
- **`TEMPLATE_TABLE_SKELETONS`**：只经 `chapter_catalog.table_skeletons` 读；复垦 `{}`。✅
- **`TEMPLATE_TABLE_DATA_RULES` / `RISK_LEVEL_CRITERIA`**：只在
  `chapter_base._build_kb_context_section` 读 —— 该方法**全仓无人调用（死代码）**，
  复垦提示词不可达。⚠️ 上报（§2.2-⑨）。
- **`FIXED_CHAPTER_KEYWORDS` / `VARIABLE_CHAPTER_KEYWORDS`**：只在 `template_content.py`
  的 `classify_section`（本身也无人调用）内使用；随 `template_content_provider` 的复垦
  分支跳过而不可达。⚠️ 上报（§2.2-⑨）。

### 2.3 上报项（未修，附精确位置与正确修法）

| 编号 | 位置 | 问题 | 正确修法 |
|---|---|---|---|
| ① | `few_shot_service.refresh_few_shot_cache`（`few_shot_service.py:272`，默认 `domain="stability"`）；调用点 `report_workflow.py:1114` 未传域；`get_cached_few_shot(ch)` 缓存键只有章号 | 复垦章会拿到**稳评**相似项目 few-shot（若同进程先跑过稳评） | `refresh_few_shot_cache` 加域、缓存键含域；调用点传 `domain_of_state(state)` |
| ② | `chapter_base.act()` `:307` `get_few_shot_with_skills(self.chapter_number)` | 读稳评 `review_skills` | 给 `get_few_shot_with_skills` 传 `self.domain` 并让 SQL 按域过滤（需 `review_skills` 有域列或按域约定过滤） |
| ③ | `chapter_base` `_refresh_learning_cache()`（`:54` 硬编码 `"stability"`、`ALL_CHAPTERS`、`_is_chapter_relevant` 稳评章号）；注入点 `_default_user_prompt` `:1982` | 复垦章拿稳评历史常见问题（且按稳评章号取） | 学习缓存按域分区；`_get_cached_learning_hints(domain, ch)`；复垦无数据即空 |
| ④ | `chapter_base.act()` `:325` `_get_expert_skill_hints(self.chapter_number)`（`master_orchestrator.py:1071`，读 `review_skills`） | 读稳评专家/用户 skill | 同 ②，域过滤 |
| ⑤ | `chapter_base.act()` `:245-257` 约束块 | 措辞「征地文号」「社区/街道」偏稳评 | 措辞按域取（复垦：批复/备案文号、村组/地块） |
| ⑥ | `chapter_base._retrieve_four_dimension`（`:1116`，`kb_four_dimension`），触发 ch4/10 | 复垦当前没有 ch4/10 agent，暂不可达；**一旦补 ch4/10 agent 即会把稳评四性案卷拉进复垦章** | 触发条件按域收紧（复垦不查 `kb_four_dimension`） |
| ⑦ | `chapter_base._maybe_run_tool_round` system `:644`「你是一个稳评报告助手。」；`_build_tool_decision_prompt` 引导词按稳评章号（`:687` 起） | 复垦工具回合顶着稳评身份、给稳评工具（勘测定界/问卷/评分） | 身份与工具引导按域取；复垦只开放复垦相关工具 |
| ⑧ | `master_orchestrator._PROJECT_CARD_LABELS`（`:36`）、`_STABILITY_KEY_LABELS`（`:201`）；`build_project_card`（`:223`） | 复垦事实卡顶着「稳评实施单位」「稳评责任单位」「征地预公告文号」「征收面积」等稳评标注 —— 会被模型抄进复垦正文 | `build_project_card(filled, domain=…)` + 经 `chapter_catalog` 取域化标注（可复用 `front_matter_labels` 的 `org_label`/`implement_label` 口径）；调用点传域（`build_chapter_prompt` 已持 `domain`） |
| ⑨ | `chapter_base._build_local_regulation_context`（`:1987`）、`_build_announcement_context`（`:2042`）、`_build_extracted_tables_section`（`:2088`）、`_build_kb_context_section`（`:2152`）、`_get_kb_word_range`（`:2271`）；`template_content.TemplateContentProvider.classify_section`（`:64`） | **全仓无人调用**的稳评遗留（读 DB32 种子 / 稳评表格规则 / 风险等级标准） | 删除（本次未删，属**既有**死代码，非本次改动引入；建议后续清理） |

### 2.4 结论

**路径 A（复垦主路径）的提示词装配，在本次修复后已不含稳评内容**：材料1 抬头、模板段落、
标准子标题、写作引导、自定义兜底身份五处稳评素材均已收口/省略；RAG 素材按域检索。
**路径 B（`report_workflow`）** 的结构类注入项（身份/结构/字数/表格参考/要求块）已按域，
但**事实卡标注（⑧）**仍带稳评字样，且 `act()` 公共下游的 few-shot / skill / 工具回合
（①②④⑦）仍是稳评来源。故**没有到底**：路径 B 与其共享的 `act()` 下游仍有稳评味内容，
已按 §2.2 逐条给出位置与修法。这些点的共同点是**读的都是稳评专有的 DB 表 / 数据文件，
需要按域过滤（DB 层）或按域取词（事实卡标注）**，非本次「材料块」同类的小改。

---

## 3. 稳评行为验证（stash-diff，非目测）

按题意以 `git stash` 对照 `97357e5`：

1. 用 `/tmp/prompt_dump.py`（stub 掉 DB 型来源：`_get_cached_learning_hints`、
   `get_cached_few_shot`、`build_template_block_for_prompt`）对**稳评**章 2–14 各章
   dump `_default_system_prompt` + `_default_user_prompt` 全文。
2. `git stash` → 以 BASE 代码同样 dump → `git stash pop`。
3. `diff /tmp/base.json /tmp/head.json` → **完全一致**（`IDENTICAL: stability sys/usr
   prompts byte-for-byte equal`）。

另对本次**迁出到缝隙**的 `act()` 字面量做逐字对照（BASE 源码里 eval 出原字面量比对）：

- `writing_guide("stability")` == BASE 内联字面量 → `True`
- `custom_prompt_persona("stability")` == `"你是一位资深稳评工程师。"` → `True`
- `headings_for_prompt("stability", n)` == `format_headings_for_prompt(n)`（n=2..14）→ `True`
- `material_heading("stability")` / `template_reference_for_prompt("stability",…)` 与 BASE
  的同名输出在 sys/usr dump 中已含入且 diff 一致 → 覆盖。

复垦侧抽检：章 1/2/3 的 `_default_user_prompt` 不含 `稳评/DB32/社会稳定风险评估/拟征收土地`，
且含复垦材料1 抬头与 `《土地复垦条例》`；模板段落块对复垦**不注入**（sentinel 打桩验证）。

---

## 4. 测试

- 新增 `tests/test_chapter_user_prompt_domain.py`（10 例）：稳评逐字 / 复垦换口径、
  `template_reference_for_prompt` 复垦**不调用提供器**、agent 级 user prompt 无稳评味、
  sentinel 证明模板段落「稳评注入 / 复垦省略」。
- 全量：`python3 -m pytest tests/ -v`
  → **414 passed / 1 skipped / 5 failed**。
  基线为 **404 passed / 1 skipped / 5 failed**（+10 = 新增用例）；5 个 failed 与基线
  逐一相同（`test_date_source_rule`、`test_hitl_resume_loop`×2、`test_survey_fixes`×2），
  系本分支既有、**未追**。

---

## 5. 自评（self-review）

- **稳评逐字**：以 stash-diff 全文比对 + 字面量 eval 比对双重确认，非目测。
- **不留死代码**：删掉本次改动使其彻底无人调用的 `_build_seed_context`，以及
  `_default_user_prompt` 里被覆盖的重复 `template_content` 块与只写不读的 `seed_ctx`。
  §2.2-⑨ 的若干死方法**属既有**、且不在本次改动面上，未一并删（避免夹带、保稳评 diff 干净），
  已列为后续清理项。
- **未新增依赖 / 未用 GIS 库**：纯文本装配改动。
- **无第二份真相**：所有按域取值都收口到 `chapter_catalog`，调用点无 `if domain == ...`。
- **测试不触网、不碰真实库**：`_get_cached_learning_hints`/`get_cached_few_shot` 打桩；
  模板提供器打桩（sentinel），断言复垦分支**连提供器都不调**。
- **遗留**：§2.3 的 ①–⑧ 仍需按域过滤（DB 层/事实卡标注），其中 ⑧（事实卡稳评标注）
  对路径 B 的复垦可读性影响最直接，建议优先。
