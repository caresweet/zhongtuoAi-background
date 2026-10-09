# 复垦会话「建出来就穿稳评装束」——最后一处收口

**分支** `feat/reclamation` ｜ **BASE** `751a8ba` ｜ 工作目录 `backend/`

## 0. 病根（一处）

`app/agent/state.py::create_initial_state` 建会话时**无条件**铺稳评的 14 章、
稳评的报告标题、稳评的模板名：

```python
for num, defn in CHAPTER_DEFINITIONS.items():          # 稳评 14 章
    chapters[num] = ChapterContent(number=num, title=defn["title"], ...)
return AgentState(
    report_title=report_title or "社会稳定风险评估报告",
    template_name="内置十四章案卷标准结构",
    ...)
```

它由 `app/routers/report.py::start_generation` 调用。**域在调用点之前就已解析**
（`detect_domain` 在同函数上方），却从未传进来 —— 于是复垦会话的
`GET /generate/{sid}/chapters` 返回 14 张稳评章，`report_title` 是
「社会稳定风险评估报告」。这正是业主反对的「混装」。

## 1. 改动：`create_initial_state` 按域取（域从调用点传入）

### 域如何到达
`start_generation` 里 `domain_id = detect_domain(...)`（已有）→ 新增形参
`domain=domain_id` 传入 `create_initial_state`。形参给 `"stability"` 默认值，
`conftest.py` 等老调用点（`create_initial_state(str(uuid.uuid4()))`）不受影响。

### before / after（`app/agent/state.py`）

```python
# BEFORE
def create_initial_state(session_id, report_title="", project_context=""):
    from app.agent.chapter_definitions import CHAPTER_DEFINITIONS   # 模块级
    for num, defn in CHAPTER_DEFINITIONS.items():                    # 稳评 14 章
        chapters[num] = ChapterContent(number=num, title=defn["title"], ...)
    return AgentState(
        report_title=report_title or "社会稳定风险评估报告",
        template_name="内置十四章案卷标准结构", ...)

# AFTER
def create_initial_state(session_id, report_title="", project_context="", domain="stability"):
    from app.agent.chapter_catalog import (
        chapter_titles, report_kind_name, template_name as _catalog_template_name)
    for num, title in chapter_titles(domain).items():                # 稳评 14 / 复垦 10
        chapters[num] = ChapterContent(number=num, title=title, ...)
    return AgentState(
        report_title=report_title or report_kind_name(domain),      # 稳评/复垦各取各的
        template_name=_catalog_template_name(domain), ...)
```

**域差异全部收口在 `chapter_catalog`（本分支唯一缝隙），`state.py` 里没有任何
`if domain == ...`。** 复用已有的 `chapter_titles` / `report_kind_name`；
新增一个缝函数：

```python
# app/agent/chapter_catalog.py
def template_name(domain):
    if domain == _RECLAMATION:
        return "内置复垦方案标准结构（知识库综合生成）"
    return "内置十四章案卷标准结构（知识库综合生成）"   # 稳评 = 改动前 report.py 写死的那个串
```

### `template_name` 放在哪、为什么是这个字符串
- 旧代码里**有两个稳评模板名**：`create_initial_state` 写 `内置十四章案卷标准结构`，
  `report.py` 又写死 `内置十四章案卷标准结构（知识库综合生成）`（后者才是起始响应
  / 历史库落库 / 建会话实际用的值，因为调用点紧接着覆盖了前者）。
- 缝隙只能产出一个串，而**必须保稳评会话可见值不変** → 缝函数取后者
  （`（知识库综合生成）` 那个）。`create_initial_state` 现在直接产出同一个串。
- 代价：`create_initial_state('sid')` 的**中间值** `template_name` 从
  `内置十四章案卷标准结构` 变为 `…（知识库综合生成）`。该字段在调用点被立刻覆盖，
  任何会话/接口都观测不到（见 §4 stash-diff 与 §3 实测）。

## 2. 其它「预置/兜底成稳评」的点

### 已修（会话创建 / 兜底时）

| # | 位置 | 原文 | 分类 | 处理 |
|---|------|------|------|------|
| 1 | `app/agent/state.py:200`（原） | 铺稳评 14 章 | 渲染内容 | 按域取 `chapter_titles(domain)` |
| 2 | `app/agent/state.py:215`（原） | `report_title or "社会稳定风险评估报告"` | 渲染内容 | `report_kind_name(domain)` |
| 3 | `app/agent/state.py:217`（原） | `template_name="内置十四章案卷标准结构"` | 渲染内容 | `catalog.template_name(domain)` |
| 4 | `app/routers/report.py:695`（原） | `template_name = "内置十四章案卷标准结构（知识库综合生成）"` | 渲染内容 | `catalog.template_name(domain_id)` |
| 5 | `app/routers/report.py:756`（原） | `state["template_name"] = template_name` | 冗余 | **删除**（被 #3/#4 取代，已失效） |
| 6 | `app/routers/report.py:993`（原） | `current_title == "社会稳定风险评估报告"`（标题录入哨兵） | 渲染内容 | 改成该域兜底标题 `_kind_name(域)`。**#2 的强制伴生改动**：否则复垦新兜底标题「土地复垦方案」会挡住用户首条消息里的标题录入 |
| 7 | `app/routers/report.py:2596`（原，`/workflow/start`） | `report_title = request.report_title or "社会稳定风险评估报告"` | 渲染内容 | 按域兜底 |
| 8 | `app/routers/report.py:2726`（原，`resume_workflow`） | `state.get("report_title", … "社会稳定风险评估报告")` | 渲染内容（防御） | 按域兜底 |
| 9 | `app/routers/report.py:3436`（原，`trigger_human_ai_rewrite`） | 同上 | 渲染内容（防御） | 按域兜底 |
| 10 | `app/routers/report.py:3304/3316`（原，`get_human_queue`） | `CHAPTER_DEFINITIONS.get(ch)["title"]` | 渲染内容 | `catalog.chapter_title(域, ch)` |

新增源码级护栏测试：`tests/test_reclamation_session_bootstrap.py`（含
`create_initial_state` 不得再引用本模块稳评章目录的断言）。

### 未修 —— 内部标识 / 防御性兜底（保持原样）

| 位置 | 内容 | 为何留 |
|------|------|--------|
| `app/routers/report.py:62+545` | `REPORT_MATERIAL_GUIDE` 键 `"stability"`/`"bidding"` | 域**键控配置**，`.get(domain,{})`；非默认到稳评。复垦缺条目是功能缺口，不是串味 |
| `app/routers/report.py:186/564/607` | `state.get("_domain","stability")` / `request.get("domain","stability")` | **域解析**默认值，非章/题 |
| `app/agent/agents/review_table_agent.py:412` | `state.get("report_title","社会稳定风险评估报告")` | 防御兜底：会话 state 恒有 `report_title`，不触发 |
| `app/agent/tools/registry.py:84` | RAG query 兜底串 | 内部检索词兜底；`domain=` 已按 ctx 传 |
| `app/agent/agents/chapter_orchestrator.py:522` | 落库时 `report_title` 兜底 | 防御兜底（`if not state.get("report_title")`），不触发 |
| `app/agent/agents/chapter_orchestrator.py:648` | `[... for i in ALL_CHAPTERS]` | 域 `chapter_structure` 为空时的兜底；正常两域都非空 |
| `app/services/workflow_service.py:419` | `ch_def.get("title","")` | 优先 `ch_data["title"]`（已按域），稳评表仅兜底 |
| `app/agent/agents/master.py:1740-1743` | `_build_template_context` 写死「十四章案卷标准结构」 | 注入 master system prompt 的 `{template_context}`；复垦的 `identity_prompt` **没有该占位符** → 复垦下算出但从不渲染（稳评逐字不变）。归为「算了不用」而非泄漏 |

### 未修 —— 生成路径内发现的同类泄漏（报告，不越界）

下列是**同一类**（拿稳评章目录给**渲染用的标题**兜底），但位于生成路径，超出本轮
「会话创建/兜底时」的界定，且该轮已声明生成路径为「已修」—— 故仅报告：

- `app/agent/agents/chapter_orchestrator.py:1521` 与 `:1539`：`_present_for_review` /
  `_request_and_wait_for_data` 里 `CHAPTER_DEFINITIONS.get(ch)["title"]` 渲染进
  `chapter_review_prompt` 与数据请求 UI。复垦会话会显示稳评章标题。
  同函数作用域内 `ch_data` 其实已带按域的 `title`，且 `domain_of_state` 在文件顶部已导入 ——
  建议后续改为 `ch_data.get("title") or catalog.chapter_title(domain_of_state(state), ch)`。

相邻（不同类：**身份串**，非章/题/「十四章」），一并记录、未改：
- `app/agent/agents/master.py:617-618`：非 bidding 一律用稳评问候语。
- `app/routers/report.py:3249`：chat 兜底的 system prompt 写死「社会稳定风险评估报告编制助手」。

> 说明：`data_analysis_agent` / `quality_review_agent` / `knowledge_agent` /
> `chapter_base` 里的 `ALL_CHAPTERS` 引用属生成路径，按本轮界定不在范围内，未审。

## 3. 实测（运行中的 :8000，`--reload`）

登录取 token：`POST /api/v1/auth/login {"username":"admin","password":"admin123"}`。
消息统一用「复垦方案：张兴矿区采输卤扩建项目临时用地土地复垦方案」/ 一条稳评题。

### 改动前（代码 stash 回 `751a8ba`）

复垦会话 —— 起始响应 `domain` 正确，但内容全穿稳评装束：

```
=== BEFORE reclamation
domain: reclamation | template_name: 内置十四章案卷标准结构（知识库综合生成）
report_title: '社会稳定风险评估报告'
chapters: 1 编制说明 | 2 编制依据 | 3 江苏省淮安市社会稳定风险评估评审表 | 4 社会稳定风险评估工作报告
        | 5 突发性事件…预案 | 6 项目基本情况 | 7 稳评工作方案 | 8 稳评方法与过程
        | 9 各利益相关者对项目的反映 | 10 …可控性分析 | 11 分析预测风险源 | 12 确定风险等级
        | 13 防范和化解风险的工作建议 | 14 附件            ← 14 张稳评章
```

### 改动后

```
=== AFTER reclamation
domain: reclamation | template_name: 内置复垦方案标准结构（知识库综合生成）
report_title: '土地复垦方案'
chapters: 1 前言 | 2 编制总则 | 3 项目概况 | 4 土地复垦方向可行性分析
        | 5 土地复垦质量要求与复垦措施 | 6 土地复垦工程设计及工程量测算 | 7 土地复垦投资估算
        | 8 土地复垦工作计划安排 | 9 土地复垦效益分析 | 10 保障措施   ← 10 张复垦章
```

稳评会话（改动后与改动前**逐字一致**）：

```
=== stability（BEFORE == AFTER）
domain: stability | template_name: 内置十四章案卷标准结构（知识库综合生成）
report_title: '社会稳定风险评估报告'
chapters: 1 编制说明 … 3 江苏省淮安市社会稳定风险评估评审表 … 14 附件   ← 14 张，逐字不变
```

## 4. 稳评一致性 —— stash-diff（对 `751a8ba`）

`git stash` 回基线跑探针、`pop` 回改动跑同一探针，`diff`：

```
53,54c53,54
< "template_name_none": "<absent-on-base>",
< "template_name_stability": "<absent-on-base>"          # 新增缝函数，基线不存在
---
> "template_name_none": "内置十四章案卷标准结构（知识库综合生成）",
> "template_name_stability": "内置十四章案卷标准结构（知识库综合生成）"
236c236
< "template_name": "内置十四章案卷标准结构",              # create_initial_state 中间值
---
> "template_name": "内置十四章案卷标准结构（知识库综合生成）",
```

`create_initial_state('sid')`（稳评）**唯一差异**就是上面第 236 行 —— 那个被调用点
立即覆盖、任何接口都观测不到的中间 `template_name`。**其余逐字段相同**：14 章、
章标题、`report_title` 全部一致。缝函数本身是新增（基线无此名）。
§3 的稳评实测（起始响应 + `/chapters`）改动前后完全一致，佐证会话可见状态未变。

## 5. 测试

`cd backend && python3 -m pytest tests/ -v`

| | passed | skipped | failed |
|---|---|---|---|
| Baseline `751a8ba` | 380 | 1 | 5 |
| 本次改动 | **384** | 1 | 5 |

新增 4 条（`tests/test_reclamation_session_bootstrap.py`）全绿；`384 = 380 + 4`。
5 条 failed 为 survey / date / HITL 历史失败，与本次改动无关（未触碰）。

## 6. 自查

- **无死代码**：删掉了被缝函数取代的写死 `template_name`、以及变冗余的
  `state["template_name"] = template_name`。`state.CHAPTER_DEFINITIONS` 仍被
  `chapter_orchestrator`（2 处）与 `workflow_service` 引用，未删。
- **无第二份缝内容**：`template_name` 只此一处；`report_kind_name` / `chapter_titles` /
  `chapter_title` 直接复用既有实现，未复制。
- **无新依赖 / 无 GIS**。
- **中文注释**：沿用既有密集解释风格，逐处标注为何留/为何改。
- **未触网 / 未碰真实库**：测试仅用内存对象；实测用运行中的 dev 服务（题目要求）。
- **回归面**：报告标题录入哨兵（§2 #6）是本次唯一「非纯替换」的伴生逻辑，
  已实测稳评路径不变 + 复垦标题可录入（哨兵按域取）。
- **未做**：生成路径内两处同类渲染泄漏（`chapter_orchestrator.py:1521/1539`）——
  按本轮界定归为生成路径，仅报告未改；相邻的「身份串」泄漏同样仅报告。
