# 复垦工作流「域没进图状态」根因修复 —— report_workflow / report_service

分支：`feat/reclamation`　BASE：`11ff834`

## 0. 一句话结论

`ReportWorkflowRunner.start()` 从会话状态往 LangGraph 状态拷贝时，**手挑的 key 列表漏了
`_domain` / `_conversation_domain`** —— 于是整条工作流里所有 `domain_of_state(state)`
一律回落 `"stability"`：**复垦会话顶着稳评的 14 章大纲跑**（第1章「编制说明」而非复垦的
「前言」）。已把这两个键补进拷贝列表；同时发现并修复**同一处遗漏的第二落点**
（`ReportSession._PERSIST_KEYS` 白名单同样漏了它们，导致**重启后域丢失**）。
稳评路径**逐字节未变**（stash-diff 验证，见 §6）。

> 注：本轮实测发现**另一个独立的、与本次修复无关的卡死**——章节生成停在 ChromaDB
> 原生调用里（§5）。它**不是**大纲不匹配造成的，本次修复也**不能**消除它。已如实记录。

## 1. 根因与修复

### 1.1 主修复：`app/services/report_workflow.py` `ReportWorkflowRunner.start()`（改后 2066–2079 行）

**之前**（手挑 key 的拷贝循环没有域键）：

```python
        if existing_state:
            for k in ("_pdf_raw_text","_project_materials","_uploaded_files",
                       "_outline","_image_catalog","filled_data","_workflow_logs",
                       "selected_personnel",
                       "selected_cert_personnel",
                       "_questionnaire_result",
                       "_company_id"):
                if k in existing_state: base[k] = existing_state[k]
```

**之后**（新增 `_domain` / `_conversation_domain`）：

```python
        if existing_state:
            for k in ("_pdf_raw_text","_project_materials","_uploaded_files",
                       "_outline","_image_catalog","filled_data","_workflow_logs",
                       # 🔴 报告域标记 —— 由 report.py 的意图识别一次性钉在会话 state 上。
                       #    **必须在图状态起点就带上**：整条工作流里所有按域分叉的消费点
                       #    （_standard_outline 的 14 章 vs 10 章、chapter_kind / target_words /
                       #    sections_for_prompt、装配器、提示词工厂）都经 `domain_of_state(state)`
                       #    取域；图状态里没有它 → 一律回落 "stability" → 复垦会话顶着稳评
                       #    14 章大纲跑（第1章「编制说明」而非复垦的「前言」）。实测 2026-10-09。
                       #    resume 走 LangGraph 检查点，起点带了这里就带得全。
                       "_domain", "_conversation_domain",
                       "selected_personnel",
                       ...
                       "_company_id"):
                if k in existing_state: base[k] = existing_state[k]
```

### 1.2 为什么这一处就够（入口点清点）

`start(existing_state=...)` 全仓**只有一个调用点**：`app/routers/report.py:2407`
（`_run_workflow_bg` 内），而 `_run_workflow_bg` 由 **3 个 POST 端点**触发：

| 触发端点 | 行 | 传参 |
|---|---|---|
| `POST /generate/{sid}/workflow/start` | `:2643` | `session.state` |
| `POST /generate/{sid}/workflow/resume` | `:2750` | `session.state` |
| 人工意见/审批继续（human-review） | `:3463` | `session.state` |

三条都经 `_run_workflow_bg` → `start(existing_state=state)`，`state` 就是 `session.state`
（`report.py` 在建会话/聊天时已把 `_domain`/`_conversation_domain` 钉进去）。**改 `start()`
一处即覆盖全部入口**，无需分别修。

### 1.3 检查点 / resume 是否需要额外处理

**不需要额外处理**。`resume()`（`:2113`）走的是同一个 `MemorySaver` + 同一 `thread_id` 的
LangGraph 检查点，`Command(resume=...)` 会**从检查点里已有的全量状态**继续——起点带了
`_domain`，检查点里就有，resume 时自然还在。（`start()` 每次用**新** `thread_id`;
resume 用中断时存下的 `_thread_id`，见 `report.py:2403`。）

### 1.4 同一处遗漏的**第二落点**：`app/services/report_service.py` `ReportSession._PERSIST_KEYS`（改后 62–76 行）

清点「还有没有别的手挑 key 列表漏了域」时发现：**会话落盘白名单**
`to_dict()` 的过滤是 `not k.startswith("_") or k in self._PERSIST_KEYS`，
而白名单里**没有** `_domain` / `_conversation_domain`（只有 `_uploaded_files` 等）。
后果：**每次重启后恢复出来的会话 `domain_of_state` 一律回落 "stability"** ——
主修复会被「重启」原样抵消（实测：重启前 `session.state["_domain"]=="reclamation"`，
重启后为 `None`，只剩非下划线的同值 `domain` 键，而 `domain_of_state` **不读** `domain`）。

已在 `_PERSIST_KEYS` 补入：

```python
        # 🔴 报告域标记 —— `domain_of_state` 只认这两个下划线键（不认同名的 `domain`），
        #    它们是**下游按域分叉的全部依据**（14 章 vs 10 章、章类型、目标字数…）。
        #    不在这里持久化 → 每次重启后 `domain_of_state` 一律回落 "stability"，…
        "_domain", "_conversation_domain",
```

（实测：新建的复垦会话在落盘后重启，`_domain` 仍在，见 §3。）

> `node_file_parse` 里另有一处「为子流程构造 state 字典」（`report_workflow.py:166`），
> 它**已经**写了 `"_domain": domain_of_state(state)`——是正确的参照写法，未动。

## 2. 端到端证据

### 2.1 修复前（实测，会话 `924fd389`，domain=reclamation）

owner 那次跑出的**图内**完成行（stability 的章题 + stability 的章 agent）：

```
[10:45:18] app.services.report_workflow | [GEN] 第1章「编制说明」生成 1002 字 (idx=0, agent=Chapter1ComposerAgent)
```

「编制说明」= 稳评第 1 章；`Chapter1ComposerAgent` = 稳评第 1 章 agent。会话级章表却是复垦的
10 章 —— 正是「图状态拿不到域 → 回落稳评」的症状。

### 2.2 修复后（实测，新建会话 `6aec1ef5`，domain=reclamation）

1. **建会话**：`POST /api/v1/reports/generate/start`（domain=reclamation）→
   会话状态 `_domain='reclamation'`，章表 `['1'..'10']`，第1章标题 `前言`。
2. **上传材料**：`POST .../upload` 从 `/Users/mac/Downloads/明远佳苑` 上传（PDF/JPG 19 个成功，
   dwg/dxf 5 个被端点拒绝——与本次无关）。
3. **起工作流**：`POST .../workflow/start`。
4. **图内证据（live）**：章节 1 生成时打印的警示来自 **`ChapterAgentBase`**，
   且缺的键是**复垦**专属：

   ```
   [10:52:51] WARNING app.agent.agents.chapters.chapter_base |
       Chapter 1: missing data keys ['build_unit', 'service_years'], generating with available data + placeholders
   ```

   `build_unit`/`service_years` 正是 `ReclamationChapter1Agent.required_data_keys`
   （`app/agent/agents/chapters/reclamation_chapter_1_agent.py:12`）。
   稳评第 1 章走的是 `Chapter1ComposerAgent`（继承 `BaseAgent`，**不是** `ChapterAgentBase`），
   **根本不会**打印这条 `Chapter N: missing data keys` 警示 —— 所以这条警示本身就是
   「图状态里的域 == reclamation、章 1 agent == 复垦 agent」的 live 证据。
   （对照：同机纯稳评运行**没有**这条警示，见 §5。）

5. **成品是否收口（组合验证）**：让**真实的 `start()`** 产出图起点状态，再喂给**真实的**
   `node_build_dynamic_outline` + `node_outline_check`（不调 LLM、不联网）：

   ```
   domain_of_state -> reclamation
     第1章  前言  [fact]            第6章  土地复垦工程设计及工程量测算  [table]
     第2章  编制总则  [fact]         第7章  土地复垦投资估算  [table]
     第3章  项目概况  [fact]         第8章  土地复垦工作计划安排  [fact]
     第4章  土地复垦方向可行性分析  [fact]   第9章  土地复垦效益分析  [fact]
     第5章  土地复垦质量要求与复垦措施  [fact]   第10章 保障措施  [fact]
   ```

6. **重启后仍不回落（持久化修复证据）**：重启后端后重新查询会话 `6aec1ef5`，
   `_domain='reclamation'` / `_conversation_domain='reclamation'` 仍在。

> **live 的「第1章『前言』生成 N 字」完成行未能拿到** —— 因为本轮所有生成都卡在
> §5 的 ChromaDB 卡死里（复垦、稳评两条路都卡）。故成品章题用「真实 `start()` + 真实
> outline 节点」的组合证据代替（上方第 5 点），并辅以 live 的复垦专属章 agent 警示
> （第 4 点）。**没有**拿到 live 完成行，这是事实，不掩盖。

## 3. 稳评验证（stash-diff，非目测）

用 `/tmp/dump_surface.py` dump 三个面：**A. 域相关纯函数目录面**、**B. `start()` 图起点面**、
**C. 会话落盘往返面**。`git stash push -- report_workflow.py report_service.py` 前后各 dump 一次，
`diff`（BASE → 修复后）**只有**下面这些差异，别无他物：

- **A 目录面**：`diff` 无任何差异 → 域相关纯函数（`standard_outline` / `all_chapters` /
  `total_chapters` / `chapter_kind` / `chapter_title`，含各域与不传域）**逐字节相同**。
- **B `start()` 起点面**：
  - `stability`：只**多**了 `_domain="stability"` / `_conversation_domain="stability"`——
    值等于原先的回落值，下游 `domain_of_state` 取值**不变**；
  - `none`（existing_state 无域）：**完全无差异**（仍走回落）；
  - `reclamation`：**多**了 `_domain="reclamation"` —— 这就是修复本身。
- **C 落盘往返面**：
  - `stability`：`persisted_keys` 多了两个键（仅落盘），但 `resolved_after_reload` 仍是
    `"stability"` —— **行为不变**；
  - `bidding` / `reclamation`：`resolved_after_reload` 由 `"stability"`（错的回落）变为
    `"bidding"` / `"reclamation"`（修复）。

**结论：稳评取值逐字节相同。** 另附一轮 live 纯稳评运行（§5）——其第 1 章仍是稳评的
`Chapter1ComposerAgent`（无复垦那条警示），行为与修复前一致。

## 4. 全量测试 vs 基线

`cd backend && python3 -m pytest tests/ -q`

- **结果：450 passed / 1 skipped / 5 failed**（8.2s）
- 基线：`449 passed / 1 skipped / 5 failed`
- 差异 **+1 passed** = 本次新增回归测试 `test_start_carries_domain_into_graph_state`；
  **5 个 failed 与基线完全同一批**（`test_date_source_rule` ×1、`test_hitl_resume_loop` ×2、
  `test_survey_fixes` ×2），均先于本分支存在，未追。
- 新增测试**红/绿已验证**：把 `report_workflow.py` stash 回 BASE 后该测试 `KeyError: '_domain'` 失败；
  带回修复后通过。

## 5. 卡死（stall）—— 观察与结论

**观察（可复现）**：

- owner 那次末段：`[10:32:52] [GEN] 第1章 idx=0` → 直到 `[10:45:18]` 才
  `[GEN] 第1章「编制说明」生成 1002 字`，`[TIMING] chapter_1 累计耗时 746.7 秒`
  （≈12.4 分钟，其间无任何章节进度日志）。
- 我本轮复现：修复后复垦运行、以及**纯稳评对照运行**（干净重启、单跑）**都在章节 1 卡住**：

  ```
  [11:37:42] [GEN] 第1章 idx=0 ...     ← 之后 7 分钟无任何输出
  ```

  现场取证：
  - 进程 CPU **0%**，事件循环停在 `uv__io_poll` 等待，**没有待触发的定时器**
    （所以 `asyncio.wait_for(...)` 的 900s / 600s 超时都**没有**触发）；
  - **没有任何 ESTABLISHED 套接字**（即**没有**在途的 LLM/网络调用）；
  - `sample <pid>` 原生栈：**210 帧落在 `chromadb_rust_bindings.abi3.so`**，
    多个工作线程阻塞在 **ChromaDB 的 Rust 原生绑定**里。

**结论**：

- 卡死发生在**章节 1 的 RAG 步骤之后**、**ChromaDB 原生调用内部**，是一个**与大纲 14/10
  不匹配无关**的独立缺陷。
- 它是**域无关**的：干净重启后**只跑稳评**同样卡（稳评路径不受本次修复影响，取值逐字节相同）。
- **本次修复不能消除它，也没有消除它。** 我没有定位到 ChromaDB 卡死的根因，**不作猜测**。
  仅记录可直接观察到的现象（原生栈在 Chroma、无在途网络、无待触发定时器、域无关、可复现）。
- 影响面：当前这台机器上**任何**跨过章节 1 RAG 的工作流都会停在这里；owner 端到端测试
  会先撞到它，再谈章节题名。

> 对 owner 的提示：若实机仍卡，可先确认这是否是 §5 的 Chroma 现象（进程 CPU≈0、
> 无 ESTABLISHED 套接字、`sample` 栈在 `chromadb_rust_bindings`），它与本修复无关。

## 6. 另两处缺陷（仅记录，未修）

- **`companies.leader_name` 列缺失**：`app/models/user.py` 声明了该列，但迁移只给 `users`
  加列，未给 `companies` 加。实测每次读公司档案都报错：
  `读取公司档案失败（company_id=1）：(sqlite3.OperationalError) no such column: companies.leader_name`
  （本轮日志 `app/services/company_service.py` 多次出现）→ 公司信息进不了报告。**先于本分支，未修。**
- **few-shot 与复垦语料是两套库**：few-shot 读 `knowledge_documents`（按域键；`reclamation`
  0 行），而给复垦灌的语料在 Chroma（`kb_reclamation_*`）。故复垦**有 RAG 素材、无 few-shot
  参考样例**。**未修。**

## 7. 自审（self-review）

- **一条缝、无冗余**：域键的耦合面只有「`domain_of_state` 读的键」与「把键带过边界的
  key 列表」两处。主修复补的是图起点的 key 列表；第二落点补的是落盘白名单——两处是**不同
  边界**（图 vs 磁盘），非同一处的重复实现。未新增第二份取域逻辑（仍唯一走 `domain_of_state`）。
- **无死代码**：改动均为在既有元组里增键 + 注释；未引入只用一次/只写不读的字段。
- **稳评不动**：§3 的 stash-diff 覆盖目录面 + 起点面 + 落盘往返面；A 面零差异，B/C 面稳评取值不变。
- **`_conversation_domain` 是否多余？** 否。除 `domain_of_state` 外，`master.py`（bidding 判定）、
  `chapter_base.py` 也读 `_conversation_domain`；两键一起带才是完整的域标记（与 `report.py`
  写入时「两键同写」一致）。
- **旧会话不回溯自愈（已知边界）**：本修复是**前向**的——修复前已落盘的会话（磁盘只有
  `domain`，无 `_domain`）重启后仍会回落。owner 的 `924fd389` 即属此类，需新建会话
  （或经 `/chat`、`POST /state` 重新钉域名）。未为旧会话加「从 `domain` 还原 `_domain`」的
  第二机制，以避免与落盘白名单修复重复。**按 owner 指示，端到端用新建会话演示。**
- **未修 §5 卡死、§6 两缺陷**：均超本次「根因修复」范围，按要求仅记录。
