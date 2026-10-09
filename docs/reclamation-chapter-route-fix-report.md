# 「生成本章」逐章生成路由修复报告

- 仓库：`/Users/mac/WorkBuddy/zhongtuo-report/backend`，分支 `feat/reclamation`，BASE `a886247`
- 提交：`d503974` fix(report): 修好「生成本章」按钮 —— 调真实方法并挂上事件队列
- 范围：只改调用点（`app/routers/report.py`），未动 `ChapterOrchestrator` 的方法签名；
  两处既有调用点与稳评行为逐字未变。

---

## 1. 缺陷与影响面

前端「生成本章」按钮 → `POST /api/v1/reports/generate/{sid}/chapter/generate`
（`app/routers/report.py:2187` `generate_chapter`）。该端点在 `event_generator` 里调用：

```python
# 改前 report.py:2213
orchestrator.generate_single_chapter(chapter, state, stream_queue)   # ← 方法根本不存在
```

`ChapterOrchestrator` 没有 `generate_single_chapter`，真名是带下划线的
`_generate_single_chapter`（`app/agent/agents/chapter_orchestrator.py:700`）。实测命中即

```
AttributeError: 'ChapterOrchestrator' object has no attribute 'generate_single_chapter'
```

**影响面：整条「逐章生成」路径是死的** —— 不是慢、不是偶发，是按钮必错。
由于该异常发生在 `asyncio.create_task` 之后、被 `await task` 的 `except` 兜住，
前端拿到的是一个 `event: error`，看起来像「生成失败」，实际是代码路径不存在。

### 顺带暴露的第二个（隐藏）缺陷

原实现在收尾处：

```python
# 改前 report.py:2228-2229
state = await task
session.state = state
```

`_generate_single_chapter` 的返回值是 **bool**（成功/失败），**不是 state**。于是即便
方法名改对，这行也会把 `session.state` 整个替换成 `True` / `False` —— 一次点击把会话
状态清成布尔值。这是同一个坏调用点里潜伏的第二个炸弹，一并修掉。

---

## 2. 第三个参数到底是什么（逐调用点）

`_generate_single_chapter` 的签名是
`(self, ch_num: int, state: dict, chapter_data_packages: dict) -> bool`。第三个参数的
真实身份**在每个调用点都是「本章数据包 dict（键=章号）」**，与事件队列无关：

| 位置 | 第三个实参 | 来源 / 说明 |
|------|-----------|------------|
| `chapter_orchestrator.py:678`（`_generate_all_chapters_parallel` 内） | `chapter_data_packages` | 由该方法入参一路传入，最终源自 `state["_chapter_data_packages"]` |
| `chapter_orchestrator.py:1178`（质量审查「重写」分支） | `chapter_data_packages` | `:1174` 明写 `chapter_data_packages = state.get("_chapter_data_packages") or {}` |
| **改前** `report.py:2213`（坏调用） | `stream_queue` | ❌ 一个 `asyncio.Queue`，**不是** dict |
| **改后** `report.py:2221` | `chapter_data_packages` | ✅ `state.get("_chapter_data_packages") or {}`，与 `:1174` 同源同写法 |

- `chapter_data_packages` 在该方法体内只被用作 `chapter_data_packages.get(ch_num, {})`
  → 得到「本章上下文」，塞进 `state_copy["_chapter_context"]`。
- `_chapter_data_packages` 的写点在 `app/agent/agents/data_analysis_agent.py:175`：
  `state["_chapter_data_packages"] = result.get("chapter_data", {})`。
- 手动点「生成本章」时该键可能尚未生成 → 回落 `{}`，方法内 `.get(ch_num, {})` 同样兜住
  （这正是 `:1174` 的既有做法，我照抄，没有发明新语义）。

### 事件队列（`stream_queue`）去哪了

**它根本不是方法参数，而是编排器自己的私有属性 `self._stream_queue`**，
由 `run_full_pipeline`（`chapter_orchestrator.py:253`）挂载；`_generate_single_chapter`
内部所有 `await agent.run(state_copy, self._stream_queue)`（`:726/:743/:748/:778/:792`）
以及 `_emit`（`:1644-1648`）都读它。因此 router 必须把自建的队列挂到
`orchestrator._stream_queue` 上，否则生成照跑、事件全落空。

---

## 3. 改了什么（file:line 前→后）与为何选这个形状

**文件：`app/routers/report.py`，函数 `generate_chapter` 的 `event_generator`（约 2202–2241）**

1. **方法名**：`generate_single_chapter` → `_generate_single_chapter`
   （`report.py:2213 → 2221`）。

2. **第三实参**：
   `state` 第三个位置由 `stream_queue` 换成
   `chapter_data_packages = state.get("_chapter_data_packages") or {}`
   （新增 `report.py:2218`，调用点 `:2221`）。
   选择「构造数据包 dict」而非「把队列塞进 dict 参数」——因为方法签名要的就是 dict，
   事件另有归属（见下）。

3. **把事件队列挂到编排器**：新增 `orchestrator._stream_queue = stream_queue`
   （`report.py:2213`）。这是 `run_full_pipeline` 里既有的挂法，直接复用。
   > 为什么不改方法签名去接收队列？任务约束「不留冗余 / 不留死代码」，且
   > 「优先修调用方，而非把方法签名拓宽到容纳两种东西」。同时签名是公共代码、
   > 被两个稳评调用点共用 —— 动它风险最大、收益最低。故只挂属性、不动签名。

4. **不再用返回值覆盖 state**：删掉 `state = await task; session.state = state`，
   改为 `await task`（`report.py:2237-2240`）。理由：`_generate_single_chapter` **就地**
   写回调用方 `state`（`:802-832` 的 `state.setdefault("chapters", {})[ch_num] = ...`
   与 `state.setdefault(k, {}).update(...)`），而 `state is session.state`（`ReportSession.state`
   是普通实例属性，非 property），同一对象，无需再赋值；返回值是 bool，赋了反而有毒。

5. **排空队列再退出**：`while not task.done():` → `while not (task.done() and stream_queue.empty()):`
   （`report.py:2226`）。`_generate_single_chapter` 的收尾会再推若干事件
   （`validation_result` / `thinking` 等），原条件在任务 done 的瞬间就退出，尾部事件被丢，
   前端看不到本章收尾。一行条件修好，无新增分支。

6. **顺手删掉本函数内未使用的 `from app.services.workflow_service import workflow_service`**
   （改前 `report.py:2203`）。该 import 在 `event_generator` 内从未被引用，属死代码，
   符合「不留死代码」约定。

**未改动**：`ChapterOrchestrator` 全部代码（含两个既有调用点 `:678`、`:1178` 及其方法体）；
`_generate_chapter_content` 等其它生成入口；任何稳评取值。

---

## 4. 如何验证

### 已跑（离线、不触网、不碰真实库）

新增 `tests/test_chapter_generate_route.py`（4 例，全绿）。直接调端点**函数**
（不走 HTTP，故不经依赖注入，自传 `_FakeUser`，与 `test_human_review_loop.py` 同法），
打桩 `report_service.get_session_for_user` 与 `ChapterOrchestrator._generate_single_chapter`：

- `test_generate_chapter_calls_real_method_with_data_packages`：断言 (a) 真方法被调用；
  (b) 第三实参 **就是** `state["_chapter_data_packages"]` 那份、是 dict、**不是** `asyncio.Queue`；
  (c) 编排器 `_stream_queue` 已挂（事件 `chapter_start` / `chapter_stream` 确实出现在 SSE 流里）；
  (d) `session.state` 仍是 dict、未被 bool 覆盖，且章节就地写回。
- `test_generate_chapter_defaults_to_empty_packages_when_absent`：无 `_chapter_data_packages`
  键时第三参数回落 `{}`（对齐 `:1174`）。
- `test_generate_chapter_surfaces_task_error`：任务抛异常时经 `event: error` 吐给前端，不静默。
- `test_orchestrator_exposes_only_underscore_single_chapter`：存在 `_generate_single_chapter`、
  不存在公开 `generate_single_chapter`（防再次写错名）。

```
cd /Users/mac/WorkBuddy/zhongtuo-report/backend && python3 -m pytest tests/test_chapter_generate_route.py -v
# 4 passed
```

### 未跑 / 不可能跑（本仓约束）

- **不做真实接口联调**：本机 :8000 上有一个在飞的工作流，任务明确禁止重启后端（此前
  有实现者中途重启、毁掉了证据）。因此**没有**对
  `POST /api/v1/reports/generate/{sid}/chapter/generate` 打真请求。
- 服务器重启后**我本会跑**的实测：
  1. `curl -N -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' \
     -d '{"chapter":4}' http://localhost:8000/api/v1/reports/generate/<sid>/chapter/generate`
     —— 期望看到 `event: chapter_start` / 若干 `event: chapter_stream` / 结尾事件，
     **不再**是 `event: error` 里带 `AttributeError`。
  2. 在一次生成后 `GET /api/v1/reports/generate/<sid>/chapters/all`，确认该章出现在
     `chapters` 里且 `session.state` 仍是对象（不是 `true`/`false`）。
- 未做 LLM 真调用（无网、且会碰外部 API）：`_generate_single_chapter` 内部的
  DataValidator/KnowledgeAgent/ChapterAgent/FormatCompliance 只有在真实运行时才触发，
  本次以打桩替身覆盖「调用契约」，未覆盖「生成质量」。

---

## 5. 全量测试对照基线

命令：`cd /Users/mac/WorkBuddy/zhongtuo-report/backend && python3 -m pytest tests/ -v`

| | passed | skipped | failed |
|---|---|---|---|
| 基线 | 450 | 1 | 5 |
| 修复后 | **454** | 1 | **5** |

`+4` 恰为本轮新增用例；**失败的 5 例与基线同 5 例**，均早于本分支存在，未追：
`test_date_source_rule::test_material_dates_are_not_asked_back`、
`test_hitl_resume_loop::TestEmptyResumeDoesNotLoop::{test_empty_resume_loops,test_filled_resume_continues}`、
`test_survey_fixes::{test_table_3_1_six_dimensions,test_table_3_3_dept_survey}`。
**无回归。**

---

## 6. 自审

- **保稳评行为**：两个稳评调用点（`:678`、`:1178`）与其方法体逐字未动；改动全部落在
  router 的坏调用点内。全量用例数只增不减、失败集不变，佐证未伤及共享路径。
- **契约正确性**：第三参数是 dict（已断言非 Queue）；事件归属 `_stream_queue`
  （已断言队列被挂且事件流出）；返回值不再污染 state（已断言）。
- **第二个炸弹**：`state = await task` 的 bool 覆盖一并修掉，未留半修。
- **`_report_session` / `generation_mode` 是否缺挂？** 已核：
  - `_generate_single_chapter` 驱动的子链（DataValidator→KnowledgeAgent→ChapterAgent→
    FormatCompliance、`_run_blocking_content_review`）**不读** `state["_report_session"]`
    （全仓该键仅 6 处，均不在子链内）；`_run_assemble_report` 才用，而本条路不汇编整文。
  - `generation_mode` 由 `_generate_single_chapter` **自身**在调 KnowledgeAgent 前就地设
    `"chapter_by_chapter"`（`:742`），router 无需代设。
  - 故不再补挂，避免引入无法在线上复验的额外行为（服务器禁重启）。
- **私属性 `orchestrator._stream_queue`**：全类无公开 setter，`run_full_pipeline` 亦如此挂；
  选择直接挂属性是「最小且诚实」的做法，未新增只为掩盖签名错配的 wrapper。
- **注释风格**：沿用现有 🔴 密注中文风格，仅描述「为何这么挂/这么传」。
- **残留**：全仓再无 `generate_single_chapter`（无下划线）引用。
- **未做**：真实接口联调、LLM 真生成（均受本机约束，见第 4 节）。
