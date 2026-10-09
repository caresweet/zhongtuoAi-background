# 复垦会话「稳评身份/章标题/报告名」渲染侧泄漏收口报告

**分支** `feat/reclamation` ｜ **BASE** `778a381` ｜ 工作目录 `backend/`

本 pass 治的是「复垦会话把**稳评章标题 / 稳评身份串 / 「社会稳定风险评估报告」报告名**
显示给用户，或喂进复垦会话会执行的提示词」这一类**渲染/可见**泄漏（区别于早前几轮
已正确留下的内部标识）。域差异一律收口在 `app/agent/chapter_catalog.py`，调用点不写
`if domain == ...`。**稳评（默认域）逐字节不变** —— 见 §4 stash-diff 实测。

---

## 1. 三处已确认站点（全部按缝隙修）

### Site 1 — 章节审核 UI / 数据请求 UI 的章标题

`app/agent/agents/chapter_orchestrator.py` `_present_for_review` / `_request_and_wait_for_data`

```python
# BEFORE（两处相同）
from app.agent.state import CHAPTER_DEFINITIONS
title = CHAPTER_DEFINITIONS.get(chapter_num, {}).get("title", f"第{chapter_num}章")

# AFTER（:1525 与 :1544）
from app.agent.chapter_catalog import chapter_title
title = chapter_title(domain_of_state(state), chapter_num)
```

**现在从哪取**：`chapter_catalog.chapter_title(会话域, 章号)`。稳评分支委托
`chapter_definitions.get_chapter_title(n)`，其取值 `CHAPTER_DEFINITIONS.get(n,{}).get("title", f"第{n}章")`
**逐字等于**原表达式 → 稳评不变。复垦第 3 章据此显示「项目概况」（而非稳评制式表名
「江苏省淮安市社会稳定风险评估评审表」）。域从 `state` 取（`domain_of_state`，文件顶部已导入）。

### Site 2 — 主对话「打招呼」身份串

`app/agent/agents/master.py:614-623`

```python
# BEFORE
if is_bidding:
    resp = "您好！我是招标投标文件编写专家。…"
else:
    resp = "您好！我是社会稳定风险评估报告编制专家。…"   # 复垦会话说「你好」→ 稳评专家

# AFTER
if is_bidding:
    resp = "您好！我是招标投标文件编写专家。…"            # 招投标就近内联，逐字不变
else:
    from app.agent.chapter_catalog import chat_greeting
    from app.domains import domain_of_state
    resp = chat_greeting(domain_of_state(state))
```

**现在从哪取**：新增缝隙函数 `chapter_catalog.chat_greeting(域)`（:364）。稳评/默认逐字
等于原句；复垦改称「土地复垦方案编制专家」。招投标那句仍留在 `master.py`（招投标不经章目录）。

### Site 3 — WS 聊天兜底的 system prompt（`:3249` 附近，上一轮未动）

`app/routers/report.py:3268-3275`

```python
# BEFORE
system_prompt = (
    "你是社会稳定风险评估报告编制助手。\n"
    "你的职责：引导用户提供项目信息，解答稳评相关问题。\n"
    "如果用户发送了文件或图片，请确认已收到并简要说明文件内容。\n"
    "风格：专业但友好，简洁直接，不说废话。\n"
    f"{data_context}\n"
)

# AFTER
from app.agent.chapter_catalog import chat_system_prompt
from app.domains import domain_of_state
system_prompt = (
    chat_system_prompt(domain_of_state(session.state)) + f"{data_context}\n"
)
```

**判定**：**可达，属「喂给复垦会话会执行的提示词」**。这是 `ws_router.websocket("/ws/{session_id}")`
（`report.py:3093`）的 `msg_type=="chat"` 兜底分支 —— WS 端点在运行、接受任何客户端的
`chat` 消息，`session.state` 里带域。当前前端只用 WS 收事件、不发 `chat`（发送走 SSE
`sendChatMessage`），所以**当前 UI 触发不到**；但只要经 WS 发一条普通聊天消息（API 级可达），
复垦会话的回复就会由「社会稳定风险评估报告编制助手」生成。按任务口径（进提示词）**修**：
新增缝隙函数 `chapter_catalog.chat_system_prompt(域)`（:373），逐行等于原段，只有身份按域换。

---

## 2. 收口缝隙（`chapter_catalog.py` 新增，均为纯字符串）

| 函数 | 稳评（默认）取值 | 复垦取值 |
|---|---|---|
| `chat_greeting(domain)` | 改动前 master.py 写死的那句 | 「我是土地复垦方案编制专家…」 |
| `chat_system_prompt(domain)` | 改动前 report.py 写死的那四行 | 「你是土地复垦方案编制助手…」 |

两者都只换「稳评」这层身份，其余逐字。稳评分支**不复制**任何实现、直接返回原字面量。

---

## 3. 「关类」——全仓 grep 的逐点分类

按任务三类串（**稳评章标题** / **稳评身份·角色串** / **「社会稳定风险评估报告」报告名**）
扫 `app/` 与 `scripts/`，逐点判定「渲染/提示词（修）」或「内部标识/不可达（留）」。

### 3.1 已修（本 pass）

| 文件:行 | 内容 | 类别 | 归属 |
|---|---|---|---|
| `chapter_orchestrator.py:1525/1544` | 章标题渲染进章节审核 / 数据请求 UI | 渲染 | Site 1 |
| `master.py:614-623` | 「你好」问候身份串 | 渲染 | Site 2 |
| `report.py:3268-3275` | WS 聊天兜底 system prompt | 提示词 | Site 3 |
| `quality_review_agent.py:1160` | 「你是一位{报告名}的质量审核专家」 | 提示词（角色串） | 同类的报告名/角色串 |
| `quality_review_agent.py:1198` | `system="你是一个专业的社会稳定风险评估报告审核专家…"` | 提示词 | 同上 |
| `data_analysis_agent.py:380` | 「放在{报告名}的哪个章节」（图片分类） | 提示词 | 报告名进提示词 |

后三处用**既有**缝隙 `report_kind_name(域)` 就地替换（稳评分支 = 「社会稳定风险评估报告」，
故稳评逐字不变，见 §4 探针）。三者所在的提示词体仍主要是稳评语义（见 §3.3），本 pass 只治
任务界定的**串**。

### 3.2 内部标识 / 防御性兜底 / 不可达 —— 保留

| 文件:行 | 内容 | 为何留 |
|---|---|---|
| `report.py:67-68` | 域清单端点里稳评域自己的 `title/description` | 是**稳评域的自我介绍**，非复垦会话产物 |
| `report.py:3457`、`chapter_orchestrator.py:522`、`review_table_agent.py:412`、`schemas/report.py:116` | `report_title` 兜底 | 会话 state 恒有 `report_title`（建会话即钉），兜底不触发；上一轮已按「防御性兜底」留 |
| `tools/registry.py:84`、`rag/retriever.py:364` | RAG 检索词兜底 | **内部检索词**，不渲染、不进系统提示词；`domain=` 已按 ctx 传 |
| `knowledge_agent.py:37` | 复垦/稳评 KB 查询串 `CHAPTER_KB_QUERIES` | 内部检索词 |
| `data_validator_agent.py:148` | `title != "社会稳定风险评估报告"` 比较 | 内部判定，非渲染 |
| `master.py:27/31` | `SYSTEM_PROMPT` 本体 | 经 `_get_domain_system_prompt` 按域取（复垦拿 `RECLAMATION_IDENTITY_PROMPT`），**复垦不执行这一段** |
| `state.py:197`、`cross_reference_agent.py:248`、`chapter_catalog.py` 注释等 | 注释 / 别名表 | 非渲染 |
| `chapter_definitions.py` / `reclamation_definitions.py` | 章目录（单一事实源） | 定义本身，非调用点 |
| `master_orchestrator.py:426/796/814/876` | `build_chapter_prompt` 内联稳评文本 | 已按域门控（`has_*` / `requirements_block` / `_ch_specific`）；§3.3 例外 |
| `reference_llm.py:36`、`chapter1_composer.py:110`、`report_styles.py`、`scoring_service.py:4` | 稳评专有管线/文案 | 复垦不走（reference/chapter1/scoring 皆稳评专有） |
| `skill_service.py:97` | 审核 skill 蒸馏提示 | 管理端离线，非会话产物 |
| `image_catalog.py` / `image_classifier.py:187` / `review_table_service/filler` | 稳评图注/评审表物件 | 装配器按 `has_review_table` / `appendix_chapter_no` 门控；复垦不挂评审表、不挂附件章 |
| `scripts/generate_report.py:79/120` | 脚本内 `report_title` 兜底 | 该脚本通篇稳评专有（洪泽区等正则、`土地征收…` 标题拼接）；复垦走 `/generate` 子进程是**另一专项** |
| `dynamic` 相关（`template_headings.py` 等） | 稳评小标题模板 | 见 §3.3（随章节提示词体一并处理） |

### 3.3 未修 —— 稳评语义**提示词体**（报告，交专项 / 业主）

这些**不是**任务界定的「章标题 / 身份串 / 报告名」孤立串，而是整段稳评语义提示词或
模块级常量（站点处无域可取），修好需**为复垦编写对应提示词正文**（内容设计，业主口径）
或**透传域**；本 pass **未动**，仅记录：

- **`chapter_base.py:1789 _default_system_prompt`（最重）** —— 这是 `ChapterOrchestrator`
  路径下复垦第 1–3 章**实际执行**的系统提示词（该路径下复垦只实现 1–3 章，见
  `get_chapter_agent` / `_reclamation_agent`）。它写死：身份「你是一位资深稳评工程师，长期做
  土地征收社会稳定风险评估。」、依据「DB32/T4013-2021」、以及「数据归位铁律」里若干**稳评章标题**
  （第4章「决策事项基本情况」/第6章「各利益相关者对决策事项的反映」/第8章…）。**已实测确认**
  复垦第 1 章拿到的就是这段（见 §4 探针外的直读：`ReclamationChapter1Agent()._default_system_prompt(...)`
  首句即稳评身份）。**未修原因**：改成复垦口径需为复垦重写该提示词正文（persona / 规范依据 /
  数据归位铁律 6 条）——属内容设计，且与「上一轮声明 `build_chapter_prompt` 已按域收口」的
  口径冲突：`report_workflow.node_chapter_generate` 走 `_use_custom_prompt` → 用按域的
  `build_chapter_prompt`（复垦无害），而 `ChapterOrchestrator` 路径**没有**设 `_use_custom_prompt`，
  故落到这段稳评提示词。**建议**：让 `ChapterOrchestrator` 路径按域也走缝隙内的 `build_chapter_prompt`
  （稳评保持现状），或为复垦补一份 `_default_system_prompt` 复垦版。此为本类**唯一未闭合的大项**。
- **`chapter_orchestrator.py:120 _inject_enhanced_context`** —— 无条件把 DB32/T4013、江苏省稳评
  规范、南京/南通印制规范等**稳评法规正文**并入 `project_context`，喂 DataAnalysisAgent。复垦亦执行。
  属「整段稳评素材注入」，非任务三串；未动。
- **`quality_review_agent.py:1166-1185`、`data_analysis_agent.py:53`、`report_workflow.py:938/1580`**
  —— 所在提示词体的**其余**稳评语义（「10章」「第5→8章风险评分」「措施前/后得分」等）保留；
  本 pass 只换了其中的报告名/角色串。
- **`app/services/image_analyzer.py:73/104`、`app/validation/diff_engine.py:46`** —— 「用于…报告配图」/
  「…报告 — 对比验证报告」里的报告名。**站点是模块级常量 / `DiffReport` 方法**，此处无 `state`/域，
  修需**透传域**（改签名 + 各调用点）；影响面小（通用图片/比对提示），未动。
- **`app/agent/agents/review_table_agent.py`** 与 `chapter_orchestrator._run_review_table` ——
  评审表 agent 在编排层**未按域门控**（装配器侧已门控 `has_review_table`），复垦仍会算出并
  `_emit_review_table`。属「稳评制式表**物件**」（非三串），归评审表专项。

---

## 4. 稳评一致性 —— stash-diff 实测（对 `778a381`）

探针 `/tmp/_stability_persona_dump.py`：在**工作树**跑一遍 → `git stash` 回基线跑一遍 → `git stash pop`
→ `diff`。仅内存对象 + `tempfile`，不触网、不碰真实库。覆盖：

- `chapter_catalog.chat_greeting / chat_system_prompt`（新增，基线 absent）
- `report_kind_name / chapter_title`（稳评）
- `master._handle_chat` 问候（default / stability / bidding）
- `_present_for_review` 章标题（default / stability × 章 1/3/5/11）
- `_request_and_wait_for_data` 数据请求标题（同上）
- `quality_review_agent._llm_deep_review` 的完整 prompt + `system`（stub LLM 记录）
- `data_analysis_agent._quick_classify_scenes` 的 `chat_with_image(text=…)`（stub LLM 记录）

**结果**：`diff` **唯一差异**是两个**新增缝隙函数**（基线 `"<absent-on-base>"` → 现给出
稳评/复垦两份取值）。**其余全部逐字节相同** —— 问候语、章标题、报告类型名、以及
`quality_review` 与 `data_analysis` 两处被改的 prompt，稳评改动前后**一字不差**。

---

## 5. 全量测试

```
cd backend && python3 -m pytest tests/ -q
→ 391 passed / 1 skipped / 5 failed
```

- 基线 `384 / 1 / 5` → **净增 7**（`tests/test_reclamation_persona_visible.py`，7 例），**失败数不变**。
- 5 个失败与基线完全一致（`date_source_rule` ×1 / `hitl_resume_loop` ×2 / `survey_fixes` ×2），
  均为本分支既存失败，**未追逐**。
- 新增用例仅用内存对象 / `asyncio.run`（本仓无 pytest-asyncio），未触网、未碰真实库。

---

## 6. 自查

- **无死代码**：三处 `from app.agent.state import CHAPTER_DEFINITIONS` 随改动删除（本文件
  已无其它 `state.CHAPTER_DEFINITIONS` 引用；`ALL_CHAPTERS` 仍在 :648 兜底用，保留）。
  `workflow_service.py` 仍用 `app.agent.state.CHAPTER_DEFINITIONS`，故该定义未变死。
- **无第二份**：`chat_greeting` / `chat_system_prompt` 只此一份；报告名/章标题复用既有
  `report_kind_name` / `chapter_title`，未复制。
- **调用点无 `if domain`**：域差异全在 `chapter_catalog`；改动点只调缝隙。
- **稳评默认**：所有缝隙函数默认域 = 稳评（返回原字面量/委托既有实现），未传域的老调用点逐字不变。
- **无新依赖 / 无 GIS**；中文注释沿用密集解释风格。
- **未触网 / 未碰真实库**：测试与探针仅用 `tmp_path`/`tempfile` 与内存对象；:8000 的 `--reload` 后端未动。

## 7. 未能做 / 存疑

- **`chapter_base._default_system_prompt`（§3.3 首条）未闭合** —— `ChapterOrchestrator` 路径下
  复垦 1–3 章仍执行稳评身份提示词体。修它是「为复垦写一份章节系统提示词」的内容设计，
  且与「该路径未设 `_use_custom_prompt`（未接 `build_chapter_prompt`）」这一**接线缺口**相关，
  故本 pass 明确**报告不改**，建议专项处理（要么接线、要么补复垦版正文）。
- `image_analyzer` / `diff_engine` 的报告名（模块常量/对象方法，站点无域）未透传，未改。
- `scripts/generate_report.py`（稳评专有脚本）未做复垦支持，未改。
- 编排层 `_run_review_table` 未按域门控（评审表物件），归评审表专项。
