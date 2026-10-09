# 第三域（土地复垦方案）下游分发收口 —— report_assembler / master_orchestrator

分支：`feat/reclamation`　BASE：`ece7cba`

## 0. 一句话结论

复垦会话的两条下游路径（报告装配、单章提示词）现在**只产出复垦内容**：
不塞稳评评审表、复垦第 3 章正文照写、章标题取自复垦目录、提示词里是复垦的章结构；
稳评（含所有不传域的默认路径）**逐字未变**（14 章提示词 + 装配出的 docx 全文做过逐字节比对）。

## 1. 收口点：`app/agent/chapter_catalog.py`（新增 13 个按域取值函数）

既有 `all_chapters / total_chapters / chapter_title / chapter_kind / standard_outline` 之上新增：

| 函数 | 消费者 | 稳评分支取值 | 复垦分支取值 |
|---|---|---|---|
| `chapter_titles(domain)` | （备用/可读性） | `get_all_chapter_titles()` | `RECLAMATION_CHAPTER_DEFINITIONS` |
| `has_review_table(domain)` | report_assembler | `True` | **`False`** |
| `appendix_chapter_no(domain)` | report_assembler | `14` | **`None`** |
| `target_words(domain, n)` | master_orchestrator | 委托 `master_orchestrator.target_words(n)` | 定义里的 min/max_words |
| `sections_for_prompt(domain, n)` | master_orchestrator | `format_sections_for_prompt(n)` | 由 `RECLAMATION_CHAPTER_DEFINITIONS` 生成 |
| `table_format_reference(domain, n)` | master_orchestrator | `get_table_format_reference(n)` | 定义里的 `key_tables` |
| `key_labels(domain)` | master_orchestrator | `stability_key_labels()` | `RECLAMATION_KEY_LABELS` |
| `report_persona(domain)` | master_orchestrator | 改动前原句 | 域 `identity_prompt` |
| `has_scoring_guidance(domain)` | master_orchestrator | `True` | `False` |
| `has_chapter_specific_requirements(domain)` | master_orchestrator | `True` | `False` |
| `requirements_block(domain, lo, hi)` | master_orchestrator | `""`（稳评走内联） | 复垦要求文本 |
| `report_kind_name(domain)` | report_assembler | `"社会稳定风险评估报告"` | 域 `display_name`=`"土地复垦方案"` |
| `cover_type_line(domain)` / `cover_fallback_title(domain)` | report_assembler | 原字面量 | `《土地复垦方案》`/`土地复垦方案` |

**稳评不变的关键**：稳评分支**不复制**任何实现，全部委托给既有函数 / 返回改动前的同一字面量；
复垦专用文本（`RECLAMATION_KEY_LABELS`、`render_reclamation_requirements`）落在 `app/agent/reclamation_definitions.py`。

## 2. 逐点改动

### A. `app/services/report_assembler.py`

| 位置（改后行） | 之前 | 之后 |
|---|---|---|
| 顶部 import | `from app.agent.chapter_definitions import ALL_CHAPTERS, chapter_no_of_heading` | 只留 `chapter_no_of_heading`；新增 `chapter_catalog` 的 7 个函数 + `domain_of_state` |
| `assemble()` ~400 | （无） | `self._domain = domain_of_state(state)`；`_chapters = all_chapters(domain)`；`_total = total_chapters(domain)` |
| 类属性 ~355 | （无） | `_domain = "stability"`（单章导出等不设域时回落稳评） |
| 原 `:446` 文号修正循环 | `for ch_num in ALL_CHAPTERS` | `for ch_num in _chapters` |
| 原 `:522` 占位清理循环 | `for ch_num in ALL_CHAPTERS` | `for ch_num in _chapters` |
| **原 `:568-603` 第 3 章评审表块** | `if ch_num == 3:` 无条件渲染稳评评审表后 `continue`（**复垦第 3 章正文因此被丢弃**） | `if ch_num == 3 and _has_review_table:`；复垦 `False` → 落到普通正文分支，第 3 章正文照写 |
| 原 `:609` 分页 | `if ch_num < TOTAL_CHAPTERS` | `if ch_num < _total` |
| 原 `:621-623` 附件章 | 无条件 `_current_chapter=TOTAL_CHAPTERS; _add_appendices(...)` | `_appendix_no = appendix_chapter_no(domain)`；非 None（稳评）才挂附件；复垦 `None` → 不挂稳评附件 |
| `_get_chapter_title` ~2448 | `self._CHAPTER_TITLES.get(ch_num, ...)`（类属性=稳评 14 标题） | `chapter_title(self._domain, ch_num)`；删除 `_CHAPTER_TITLES` 与 `get_all_chapter_titles` 导入 |
| `_add_appendices` ~4146 | `from ...chapter_definitions import TOTAL_CHAPTERS as _TOTAL` | `_TOTAL = appendix_chapter_no(domain) or total_chapters(domain)` |
| 页眉抬头 ~690 | `... + "社会稳定风险评估报告"` | `... + report_kind_name(self._domain)` |
| 输出文件名 ~703 | `f"{safe_name}_社会稳定风险评估报告.docx"` | `f"{safe_name}_{report_kind_name(self._domain)}.docx"` |
| 封面兜底标题 ~2009 | `'土地征收决策'` | `cover_fallback_title(self._domain)` |
| 封面副标题 ~2023 | `'《社会稳定性风险评估》'` | `cover_type_line(self._domain)` |

未改（见 §6）：公司简介/工作组、人员默认名单、证书说明句、封面落款标签等「出具方前置件」。

### B. `app/services/master_orchestrator.py`

- 新增 `domain: str = "stability"` 形参；顶部一次 `from app.agent import chapter_catalog as _catalog`。
- `_KEY_LABELS` 提到模块级 `_STABILITY_KEY_LABELS` + `stability_key_labels()`，函数内改 `_KEY_LABELS = _catalog.key_labels(domain)`。
- 身份首句：`你是社会稳定风险评估报告的资深编写专家。` → `{_persona}`（`_catalog.report_persona(domain)`）。
- 评分块：`if _catalog.has_scoring_guidance(domain) and any(kw in title ...)`。
- 字数：`target_words(num)` → `_catalog.target_words(domain, num)`。
- 章节结构：`format_sections_for_prompt(num)` → `_catalog.sections_for_prompt(domain, num)`。
- 表格参考：`get_table_format_reference(num)` → `_catalog.table_format_reference(domain, num)`。
- 逐章特定要求：`{_get_chapter_specific_requirements(num)}` → `{_ch_specific}`（复垦为 `""`）。
- **末段「写作要求」块按域择一**：`_req_block = _catalog.requirements_block(domain, lo, hi)`；复垦追加复垦文本，
  稳评（`""`）走 `else:` 分支的原内联正文（含 `_old_project_block`/`_regulation_whitelist`/`_ch_specific`/
  专家 skill 反馈——这些稳评专有的 DB/文件读取因此**不再**注入复垦提示词）。
- 复垦要求文本落在 `reclamation_definitions.render_reclamation_requirements`（复垦口径，无 DB32/稳评术语）。

### C. `app/services/report_workflow.py`

- `node_chapter_generate` 里 `build_chapter_prompt(...)` 调用补 `domain=_domain`（域在函数内已解析）。
  少这一行，复垦章仍会拿到稳评提示词——这是端到端的关键一行。

## 3. 复垦会话现在产出什么 + 证据

- **不插稳评评审表**：`has_review_table("reclamation") is False` → 第 3 章评审表块整段不进入。
  端到端渲染断言 `"评审表" not in 文档全文`。（`tests:test_reclamation_render_writes_ch3_and_has_no_review_table`）
- **复垦第 3 章正文写出**：第 3 章落到普通正文分支，`_add_chapter` 渲染。
  端到端断言 `RECLAMATIONBODY`（我塞进第 3 章的唯一串）出现在文档里。
- **章标题是复垦的**：`_get_chapter_title` 走 `chapter_title("reclamation", n)`（如 3→「项目概况」）。
  目录/章标题页/`_add_chapter` 三处都经它。
- **无稳评附件章**：`appendix_chapter_no("reclamation") is None` → `_add_appendices` 不跑，
  断言 `"附件清单" not in 全文`。
- **提示词是复垦结构**：对复垦全部 10 章断言：提示词含复垦二级标题（`## 3.1 项目简介`…）、
  复垦身份首句、复垦字数区间；且**不含** `社会稳定风险评估/稳评/DB32/征地/征收/评审表` 任一标记。
- **报告类型印记**：封面副标题、页眉抬头、输出文件名按域取（复垦 = 土地复垦方案）。

### 钉在「决策点」而非端到端的部分
- `has_review_table` / `appendix_chapter_no` 两个判据（决定评审表路径是否进入、附件章是否挂）——
  既有端到端断言，也有单点断言。
- 编排层是否把域透传给提示词构造器：用 `inspect.getsource` 钉住调用点含 `domain=_domain`，
  **不做**完整 workflow 节点级联（会拉起 LLM/RAG）。这条是「少一行就前功尽弃」的接缝，故单独钉。

## 4. 稳评会话产出什么 + 为何未变

- **提示词**：`stability` 与「不传 domain」两次生成的文本**逐字节相等**（`tests:test_stability_prompt_unchanged_by_default_domain`）。
  更强证据：以 base `ece7cba` 的工作树与改后逐章比对——**14 章提示词全部 byte-identical**（脚本核对，见 §5 记录）。
  原因：稳评分支的每个 `_catalog.*` 都返回**改动前的同一函数/同一字面量**，末段要求块仍是原内联正文。
- **docx 全文**：对 3/4 两章（含评审表章与普通章）的装配产物做段落 + 表格 + 页眉页脚文本 dump，
  改动前 vs 改动后**完全一致**（`STABILITY_DOCX_IDENTICAL`）。评审表仍插入、第 3 章 LLM 正文仍被丢弃、
  附件仍挂第 14 章、标题/文件名不变。
- 端到端断言：`tests:test_stability_render_keeps_review_table_and_discards_ch3_llm_body`。

## 5. 验证记录（本机实测）

1. 逐字节提示词比对：在 base 提交上 `git stash` 出改动前代码生成 14 章提示词，`pop` 后用改后代码再生成，`diff` → 全 IDENTICAL。
2. 逐字节 docx 文本比对：同上方法对 `domain=stability` 的 `assemble()` 产物做文本 dump，`diff` → IDENTICAL。
3. 复垦 10 章提示词的稳评标记计数全部为 0。

## 6. 未做 / 已知残留（重要，请评估）

这些**不在任务列举的改动点内**，且需要**复垦专有文案决定**，本 pass 未动；它们在复垦报告里仍会出现
「稳评」字样（均属「出具方前置件」，非章次/评审表）：

- `report_assembler._add_company_intro`：小节标题「稳评工作组人员及分工情况」；回退人员名单里的角色名「稳评负责人」。
- `report_assembler` 证书说明句：「本报告相关机构营业执照、项目人员职称及**稳评**培训证书详见**附件1**。」
  （复垦无附件章，「附件1」引用同时失真）。
- 封面落款标签「稳评责任主体：」「稳评实施单位：」。
- `_add_appendices` 内的附件分类名、`_fill_table_data` 等仍是稳评表结构（**仅在复垦会话有图片/触发填表时**才显形；
  复垦当前不挂附件章，故附件分类名暂不出现）。
- 装配器生成的 Word 批注作者「稳评审核Agent」（`w:author`）。

原因：把这些改对需要为「复垦方案」定义出具方前置件文案（工作组名/角色/证书说明/落款标签），
属内容设计而非本任务的域分发收口；且没有干净的止损点（改 4 处仍剩若干处），故统一列为残留，
留给「复垦前置件」专项。

另：`master_orchestrator.generate_outline`（含 `ALL_CHAPTERS` 字号硬编码）在全仓**无调用者**，是既存死代码，
与本次改动无关，未在本次删除范围内。

## 7. 测试

- 新增 `tests/test_reclamation_domain_dispatch.py`（10 个用例）：catalog 按域取值、稳评默认不变、
  提示词双域、装配器端到端双域、编排层透传。
- 全量：`python3 -m pytest tests/ -q` → **366 passed / 1 skipped / 5 failed**。
  基线 356 passed / 1 skipped / 5 failed（survey / date / HITL 五个既有失败）——**新增 10 个用例，失败数不变**。
- 测试全部用 `tmp_path`，不触网、不碰真实 `data/*.db` 与 Chroma。

## 8. 自评

- `chapter_catalog` 成了按域取值的收口点，两个消费者各自不再 `if domain == ...`（仅提示词末段
  `_req_block` 择一——因为稳评那份必须内联保持逐字，已注释说明）。
- 删除：`report_assembler._CHAPTER_TITLES` 类属性、`ALL_CHAPTERS` / `get_all_chapter_titles` 导入
  （均因改用 catalog 而变死）。
- 风险点：`master_orchestrator` 末段用 `else:` 包住原大 f-string，靠「多行字符串不受缩进影响」保持字节不变——
  已用逐字节比对证明 14 章全等，非推测。
