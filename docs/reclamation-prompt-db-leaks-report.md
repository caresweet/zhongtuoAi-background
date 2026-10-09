# 复垦单章提示词 —— 最后一轮「稳定性泄漏」（读库/标注类）收口报告

BASE: `d538cac` ｜ 分支: `feat/reclamation` ｜ 工作目录: `/Users/mac/WorkBuddy/zhongtuo-report/backend`

上一轮 sweep（`reclamation-prompt-sweep-report.md` §2.3）把「提示词路径尚未干净」的原因
归为两类：**结构类**（已收口到 `chapter_catalog`）与**读稳评专有 DB 表 / 数据文件**的
稳定性校准数据。本轮逐条关掉后一类里 sweep 点名的 5 项，并顺手关了同一族的两处
（`build_project_card` 的三个附带小节抬头、两处兜底拟稿 prompt 的报告名）。

所有按域取值一律收口在 `app/agent/chapter_catalog.py`（本文件是域差异的唯一缝隙），
调用点不写 `if domain == ...`。**稳评（默认域）逐字不变**，见 §6（stash-diff）。

---

## 1. `build_project_card` 字段标注（sweep ⑧，命中两条生成路径）

### 读的是什么（before）
`app/services/master_orchestrator.py` `build_project_card(filled_data)`
（`:233`）里，字段标注写死取自模块级 `_PROJECT_CARD_LABELS`（`:36`）—— 满篇稳评口径
（`稳评实施单位` / `责任单位（稳评责任单位）` / `征地预公告文号` / `征收面积（亩）`…）。
另有三个**附带小节**的抬头与字段标注同样写死：
`—— 征收土地预公告正文摘录 ——`、`—— 勘测定界明细（第6章 1.5 …第10章合法性论证用）——`、
`—— 座谈会记录（第9章 各方意见汇总取材）——`，以及各自的字段行（含 `稳评公示期（公示期一周）`）。
复垦事实卡因此顶着稳评字段名与稳评章号被喂给每一章。**该函数被两条生成路径共用**：
`master_orchestrator.build_chapter_prompt`（路径 B 直写）与
`report_workflow` 的 `_draft_field_fallbacks` / `_draft_gap_fills`（兜底拟稿），
另有 `reference_llm.build_rewrite_prompt`、`ai_gap_writer` 两处调用。

### 读的是什么（after）
- 新增缝隙函数 `chapter_catalog.project_card_labels(domain)`（`:273`）与
  `chapter_catalog.project_card_lexicon(domain)`（`:293`）：
  - 稳评 → 委托 `master_orchestrator.stability_project_card_labels()`（新增，`:223`，
    返回 `_PROJECT_CARD_LABELS` 的副本，不复制第二份）；三小节抬头/字段逐字等于改动前。
  - 复垦 → 字段标注复用其**既有**的 `RECLAMATION_KEY_LABELS`
    （与「可用数据」标注同源，不另造）；**无**「征收预公告」「稳评座谈会」两小节
    （字段表留空 → 整块不出现）；勘测定界小节抬头去掉稳评章号（改 `—— 勘测定界明细 ——`）。
- `build_project_card(filled_data, domain=None)`（`:233`）改用缝隙取标注与抬头。
  未知字段回落原 key（`.get(k, k)`）—— 复垦没有的稳评字段（户数/支持率/各日期）
  即「标注缺失」，**不拿稳评的字段名顶替**。
- 五处调用点全部传域：`build_chapter_prompt`（`:604`，已有 `domain` 形参）、
  `_draft_field_fallbacks` / `_draft_gap_fills`（新增 `domain` 形参，由
  `domain_of_state(state)` 传入）、`reference_llm`（`:110`）、`ai_gap_writer`（`:111`）。

### 顺带关掉的同族两处
两个兜底拟稿 prompt 里的「下面是一份**社会稳定风险评估报告**里…」改为
`f"下面是一份{report_kind_name(domain)}里…"`（`report_kind_name` 是既有缝隙函数，
稳评域逐字等于 `社会稳定风险评估报告`）。

### 关于 sweep 提到的 `build_chapter_prompt._KEY_LABELS`
`master_orchestrator.build_chapter_prompt` 里的 `_KEY_LABELS = _catalog.key_labels(domain)`
（`:607`）**已是域感知**（上一轮收口，sweep 表 #28），**不是**同一处泄漏，无需再改。
它服务于「可用数据」小节；本轮改的是「事实卡」小节，两者字段集不同
（`_STABILITY_KEY_LABELS` ≠ `_PROJECT_CARD_LABELS`），故不合并。

---

## 2. `refresh_few_shot_cache` 按域分区（sweep ①）

### before
`few_shot_service.py` `refresh_few_shot_cache(project_info, domain="stability")`（`:283`）
缓存 `_few_shot_cache` 的键**只有章号**（`examples[ch]`），且 `report_workflow.py:1117`
的调用点**不传域** → 复垦章与稳评同号即拿到稳评相似项目的范文。
`_extract_chapter` 也按**稳评** `chapter_definitions` 的标题切段。

### after
- 缓存按域分区：`examples` 键 = `"{域}:{章号}"`，`project_hash` / `updated` 按域各存一份
  （新增 `_few_shot_key`）。
- `refresh_few_shot_cache` 只遍历**该域**的章号（`chapter_catalog.all_chapters(domain)`：
  复垦 10 章、非稳评 14 章），并把 `domain` 透给早已域感知的
  `find_best_matches(..., domain=domain)`（**复用**既有机制，未另造第二条）。
- `get_cached_few_shot` / `get_few_shot_with_skills` / `_extract_chapter` 全部加 `domain`
  （默认 `"stability"`）；`_extract_chapter` 的章标题改走 `chapter_catalog`。
- 调用点传域：`report_workflow.py:1117` → `domain_of_state(state)`；
  `chapter_base.act()/ _default_user_prompt` 两处 → `self.domain`。

### 复垦现在回空
知识库 `knowledge_documents` 里只有 `stability`(69) 与 `bidding`(3)，**无 `reclamation` 行**
（只读核对真实库），故 `find_best_matches(domain="reclamation")` 的候选为空 →
复垦 few-shot 缓存为空 → **复垦拿到「没有」**。

---

## 3. `get_few_shot_with_skills` / `_get_expert_skill_hints` 按域过滤（sweep ②④）

### before
两者各自直接读 `knowledge_base.db.review_skills`，SQL **无域过滤** → 复垦章拿到稳评的
专家/用户 skill 提示。

### after
- `get_few_shot_with_skills(chapter_num, domain="stability")`（`:320`）：两条
  `review_skills` 查询各加 `AND domain=?`。
- `master_orchestrator._get_expert_skill_hints(chapter_num, domain="stability")`（`:1075`）：
  两条查询各加 `AND domain=?`；call site 传 `self.domain`（`chapter_base.py:334`）与
  `domain`（`master_orchestrator.py:810`）。

### 选了哪条路、为什么
`review_skills` 表**本就有 `domain` 列**（`knowledge_db.py` 建表即带，默认 `'stability'`；
`skill_service.py:285` 早就在按域查），故**做「按域键查」而不是「另建复垦机制」**：
复垦域在真实库里 **0 行**（只读核对：`review_skills` 共 169 行，全 `stability`）→
复垦拿到**空串**，而不是稳评的提示。稳评域因所有行都是 `stability`，过滤后结果**逐行不变**。

---

## 4. `_get_cached_learning_hints` 取会话域（sweep ③）

### before
`chapter_base._refresh_learning_cache()` 硬编码 `"stability"`，遍历稳评 `ALL_CHAPTERS`(14)，
`_learning_cache["hints"]` 键只有章号 → 复垦章拿到稳评历史常见问题。

### after
- `_learning_cache` 改为 `{"hints": {域: {章号: [...]}}, "updated": {域: ts}}`。
- `_refresh_learning_cache(domain="stability")` / `_get_cached_learning_hints(chapter_num, domain="stability")`
  全部带域；章号取自 `chapter_catalog.all_chapters(domain)`；
  `learning_service.get_common_issues(domain, …)` 按域取（`generation_feedback.domain`）。
- 调用点 `chapter_base.py:2001` 传 `self.domain`。
- 顺带删除 `_refresh_learning_cache` 里**写而不读**的 `build_learning_hints(...)` 死调用
  （其返回值从未被消费，纯读无副作用，删之不影响任何输出）。
- `_is_chapter_relevant` 的章号映射仍是稳评参照件的（6/9/12…）—— 但它只在
  `common` 非空时起作用，而只有稳评域有反馈数据，故不会误判复垦章（已在注释说明）。

### 复垦现在回空
`generation_feedback` 真实库里 **39 行全 `stability`**，无 `reclamation` 行 →
复垦域的 `get_common_issues` 返回 `[]` → **复垦拿到空**。

---

## 5. 工具回合的身份与工具表（sweep ⑦）

### 读的是什么
`chapter_base._maybe_run_tool_round`（`:642` 起）把 `ALL_TOOLS_SCHEMA` 全量发给模型，
system 身份写死 **「你是一个稳评报告助手。」**，`_build_tool_decision_prompt` 的工具引导按
**稳评章号**给（第6章 表6-1 / 第3章问卷 / 第6·8章评分），`_build_tool_context` 的
`"domain"` 硬编码 `"stability"`。注册表里的**五件工具全为稳评器物**：
`calc_risk_scoring`（DB32/T4013 扣分制）、`query_survey`/`query_dept_survey`（稳评公众/部门问卷）、
`retrieve_rag`（描述点名「稳评规范（DB32/T4013-2021）」）、`parse_kd_table`（稳评勘测定界表型）。

### 改法
- 新增缝隙函数 `chapter_catalog.tool_round_schema(domain)`（`:659`）：
  稳评 → `ALL_TOOLS_SCHEMA` 全量（逐字不变）；**复垦 → 空表**。
- `_maybe_run_tool_round` 见空表**整轮跳过**（`:652`），连决策 prompt 与那句稳评身份串
  都不再发给模型；`_build_tool_context` 的 `"domain"` 改 `self.domain`（`:742`）。

### 身份与工具表：我的判断
- **身份**：确系稳评措辞（「你是一个稳评报告助手。」）。改法上我**没有**为复垦写一个
  平行的身份串 —— 因为复垦的工具表为空、整轮在发身份串**之前**就跳过了，那句身份串
  在复垦路径上**不可达**（写一个用不到的分支即死代码）。故复垦提示词里**不会出现**它。
- **工具表**：**不是**域名中立的。三件是彻底的稳评专有（DB32 扣分制 / 稳评问卷）；
  另两件（RAG 检索、勘测定界）行为上跨域可用，但描述里点名稳评规范/表型。
  按「复垦宁可拿到『没有』也不拿稳评的一套顶替」，复垦**整轮关闭**，而不是把稳评工具
  换个词翻译一份。

### 欠业主的决策（product decision，我没替做）
复垦应当开放哪些工具？可选项与前置条件：
1. RAG 检索 —— 需给一份**去稳评点名**的工具描述（现描述写死 DB32/T4013）；
2. 勘测定界解析 —— `parse_land_survey` 解的是稳评勘测定界表型，需确认复垦临时用地
   勘测定界报告是否同表型；
3. 复垦是否需要「指标体系/费用估算」类工具 —— 现无实现。
在拍板前，复垦工具回合保持关闭（复垦的 RAG 素材由 `act()` 统一注入、勘测定界由工艺流程解析，
不受影响）。

---

## 6. 稳评行为验证（stash-diff，非目测）

对**稳评**逐章 dump `chapter_base._default_system_prompt` + `_default_user_prompt`
（章 1–14）以及 `build_project_card`（全字段样例 / 空样例），共 30 个条目；
DB/文件型来源（`_get_cached_learning_hints` / `get_cached_few_shot` /
`_get_expert_skill_hints` / 模板底稿 / 模板段落）一律打桩，只为隔离本轮的收口改动。

```
# HEAD
PYTHONPATH=. python3 /tmp/prompt_dump2.py /tmp/head3.json
# stash → BASE 代码 → 同样 dump → pop
git stash push -m cd-stashdiff -- <7 个改动文件> && ... /tmp/base2.json ; git stash pop
python3 -c "compare base2 vs head3"
→ KEYS 30 DIFFS 0
```

即：**稳评 14 章的 sys/usr prompt 与事实卡输出，改动前后逐字节相同。**

DB 型查询另做**行级**核对（只读真实库，未写）：
- `_get_expert_skill_hints` 的 `user_requirement` 与 `text` 两条查询，加域过滤前后
  返回的 `id` 序列**相同**（`old==new`，分别 0 行 / 8 行）—— 因 `review_skills` 全 `stability`。
- `get_few_shot_with_skills` 同理（同一张表）。
- `chapter_catalog.all_chapters/chapter_title("stability", n)` 委托结果与
  `chapter_definitions.ALL_CHAPTERS / get_chapter_title` 逐一断言相等。

---

## 7. 测试

新增 `tests/test_prompt_db_leaks_domain.py`（14 例）：事实卡标注缝隙契约、稳评事实卡逐字、
复垦事实卡无稳评字样、复垦省略预公告/座谈会小节、逐字抬头、路径 B 传域、few-shot 缓存分区、
`refresh_few_shot_cache` 取域章号与域、`_get_expert_skill_hints` 域过滤、
`get_few_shot_with_skills` 域过滤、学习缓存读本域桶、工具表按域、复垦工具回合被跳过。
全部用 `tmp_path` 造 `knowledge_base.db`，**不触网、不碰真实库**。

全量：`python3 -m pytest tests/ -v`
→ **428 passed / 1 skipped / 5 failed**。
基线 **414 / 1 / 5**（+14 = 本轮新增）；5 个 failed 与基线**逐一相同**
（`test_date_source_rule`、`test_hitl_resume_loop`×2、`test_survey_fixes`×2），
系本分支既有、**未追**。

---

## 8. 最终结论

**复垦单章提示词路径，在本轮收口后已不含稳评内容（本轮点名的 5 项全部关闭）。**
两条生成路径（`chapter_base` 默认提示词 / `master_orchestrator`+`report_workflow` 直写与拟稿）
里，事实卡标注、三个附带小节抬头、few-shot、`review_skills` skill 提示、学习提示、
工具回合，**均按域取**，且复垦域因**无数据**而回**空**（而非回落稳评）：

| 读库点 | 复垦为何回空 |
|---|---|
| few-shot 缓存 | `knowledge_documents` 无 `reclamation` 候选行 |
| `review_skills`（skill 提示，两处） | `review_skills` 无 `reclamation` 行（169 行全 stability） |
| 学习提示 | `generation_feedback` 无 `reclamation` 行（39 行全 stability） |
| 工具回合 | 无复垦口径工具 → 整轮关闭 |

### 仍留在域内、但**不是**稳评内容的复垦缺口（本轮未做，如实列出）
1. **事实卡的 key 列表仍是稳评那份**（`build_project_card` 的 `keys`）。复垦自己的字段
   （`build_unit` / `service_years` / `reclamation_direction` / `dynamic_total_investment` /
   `build_content`…）**不在**该列表里 → 即便有值也**不进**事实卡；反之稳评专有字段若被
   共享解析器误填，会以**原 key**（如 `implement_unit：…`）出现 —— 已**不套稳评标注**，
   但仍是「复垦事实卡缺自家字段」。正确修法需业主给出复垦的「事实卡字段清单」，故未替做。
2. 复垦仅实现第 1–3 章 agent（`agents/chapters/__init__.py`），路径 B 对第 4–10 章
   `get_chapter_agent` 返回 `None` → 章节产出为空（`report_workflow.py:1262` 处）。属既有、
   非本轮面。

### 未新增依赖 / 未用 GIS 库
纯文本装配 + 现有 sqlite 查询改动。无新依赖。

---

## 9. 自评（self-review）

- **稳评逐字**：stash-diff 全文比对（30 条目，0 diff）+ DB 查询行级核对（只读），非目测。
- **不留死代码**：删除 `_refresh_learning_cache` 里写而不读的 `build_learning_hints` 死调用；
  删除 `few_shot_service` 因收口而**无人再用**的 `from app.agent.chapter_definitions import ALL_CHAPTERS`；
  `chapter_base` 同理删 `ALL_CHAPTERS` 导入。`_learning_cache`/`_few_shot_cache` 由标量改按域字典，
  旧形状无残留。
- **无第二份真相**：域差异全部收口在 `chapter_catalog`（`project_card_labels` /
  `project_card_lexicon` / `tool_round_schema`），调用点无 `if domain == ...`；复垦字段标注
  **复用**既有 `RECLAMATION_KEY_LABELS`，未复制。
- **测试不触网/不碰真实库**：`tmp_path` 造库 + 打桩；真实库仅**只读** `sqlite3 ... mode=ro` 核对。
- **遗留**：§8 的两点，其中「事实卡 key 列表」对路径 B 的复垦可读性影响最直接，建议优先。
- **工具回合身份**：见 §5 —— 未给复垦写平行身份串（会造成不可达分支，属死代码），
  整轮对复垦关闭，并列出欠业主的工具决策。
