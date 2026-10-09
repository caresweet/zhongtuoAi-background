# 复垦最后两处泄漏收口报告

BASE `63f8ced`（分支 `feat/reclamation`）。两处都按「稳评逐字节不变、复垦不混稳评内容」收口。

- **Item 1**：`app/validation/content_guardrails.py` —— `full_guardrail_check` 的域护栏块对稳评**不再读取**。
- **Item 2**：`app/services/image_catalog.py` —— 正文配图的「章→图规格」与「图注」按域取，
  复垦不再被盖上稳评图注。

---

## 1. Item 1：域护栏块只对非稳评域生效

### 改动

`full_guardrail_check` 里那段「域自定义护栏（`DomainConfig.guardrails`）」原先对**每个域**（含稳评）都跑：
从 `get_domain(domain).guardrails` 取串，命中正文即计入 `total_issues`。
而稳评的 `DomainConfig.guardrails` 在本块出现前**从未被读过**。故加了一句门控：

```python
domain_issues = []
if domain != "stability":          # 🔴 稳评直接跳过：不读、不计
    try:
        from app.domains import get_domain
        for rule in (get_domain(domain).guardrails or []):
            if rule and rule in markdown:
                domain_issues.append(f"[{domain}] 命中域护栏：{rule}")
    except Exception:
        pass
```

其余门控（`STABILITY_ONLY_CHECKS` 那一套）原样未动。稳评的 `total_issues` 里
`len(domain_issues)` 恒为 0 → 与改动前逐字相同。

### before/after（稳评调用）

稳评现有护栏为 `["", "风险打分严格按 DB32/T4013-2021"]`（第一条被 `if rule` 滤掉）。
构造一段**逐字包含**第二条的正文，即可看出差别：

| 正文 | 改动前 `total_issues` | 改动后 `total_issues` |
|---|---|---|
| `报告结论：风险打分严格按 DB32/T4013-2021 的口径执行。` | **1** | **0** |
| `本项目问卷调查支持率为98%，反对率2%。`（普通文本） | 3 | 3 |
| `社会稳定风险评估流程图见下图。` | 0 | 0 |

即：改动前稳评的护栏串一旦逐字出现在正文里就会 +1；改动后恒不参与。
普通文本两版一致（本节表格由 stash-diff 实测得，见 §3）。

### 新增测试

`tests/test_guardrails_domain_grouping.py::test_stability_total_issues_immune_to_domain_guardrails`
—— monkeypatch 把稳评域的 `guardrails` 换成一条**逐字命中正文**的串，断言
`full_guardrail_check(text, domain="stability")` 与基线**整份 dict 逐字节相同**、
`total_issues` 不变；同时反证同一护栏在复垦域**会**被计入（证明门控存在且只跳过稳评）。

---

## 2. Item 2：复垦正文配图不再用稳评图注

### 现状（泄漏点）

`build_image_catalog` 原先直接读两个稳评专用常量：
`CHAPTER_IMAGE_SPECS`（按**稳评章号** 6/7/8/9 挂标签，含「社会稳定风险评估流程图」）
与局部 `_FALLBACK`（「拟征收土地位置示意图（公告附图）」「拟征收地块位置示意图」…）。
复垦报告带图时，这些名字被印成正文图注。

### 改动（收口在 `chapter_catalog`，`image_catalog` 内不写 `if domain`）

`app/agent/chapter_catalog.py` 新增两个函数：

- `chapter_image_specs(domain)` —— 该域「章→图规格」表。
  稳评**委托** `image_catalog.CHAPTER_IMAGE_SPECS`（同一对象，逐字不变）；
  复垦**不回落稳评那张表**，给每章一条**不限定类别**的中性兜底规格
  （`type="figure"`、`label="<章标题>示意图"`、`min=0`、`max=4`）。
- `image_caption(domain, chapter_number, category, *, rule_label, spec_label, first_spec_label)` ——
  某张已绑定到 `(域, 章号)` 的图该印什么图注。
  稳评：历史三档优先级「材料规则标签 > 命中规格标签 > `IMAGE_CAPTION_FALLBACK` > 首规格标签」，
  逐字等于改动前取值；复垦：一律 `f"{章标题}示意图"`。

`image_catalog.py` 只做两件事：把局部 `_FALLBACK` **提为模块级常量** `IMAGE_CAPTION_FALLBACK`
（内容逐字不变，留作稳评委托源），并让 `build_image_catalog` **多一个 `domain: str = "stability"` 形参**，
内部用上面的 seam 取规格与图注。调用点（`report_workflow.node_chapter_generate`、
`report_assembler._prepare_image_catalog`、`scripts/gen_one_chapter.py`、
`scripts/export_chapter.py`）各传自己的域。

### 复垦图片现在的图注

公式 = **该图片所绑定复垦章的标题 + 「示意图」**，域中立、绝不点名稳评器物：

| 材料文件夹（沿用既有 hint） | 绑定章 | 复垦图注（现在） |
|---|---|---|
| `位置图/` | 6 | 土地复垦工程设计及工程量测算**示意图** |
| `评估流程/` | 7 | 土地复垦投资估算**示意图** |
| `公示照片4/`、`村民开会现场/` | 8 | 土地复垦工作计划安排**示意图** |
| `图片-稳评（报告中/` | 9 | 土地复垦效益分析**示意图** |
| `专家评审会照片/`、`勘测定界图/` | 附件 | （不落正文，同稳评） |

（`chapter_catalog.image_caption("reclamation", 3, "map") == "项目概况示意图"` —— 任务给的公式示例。）

### 仍未决定（交业主）

**复垦的「章 → 图片类型」规格本身**没有做 —— 任务明令不得臆造。具体地：

1. **图片绑到哪一章**仍沿**既有**的文件夹 hint 机制（`FOLDER_CHAPTER_HINTS` / `_classify_chapter_hint`，
   按稳评章号 6/7/8/9 判）。这些章号对复垦语义不对（复垦第 6 章是「工程设计」而非「项目概况」）。
   本 pass 只把**图注**改成域中立，**没动绑定映射** —— 真正的复垦章号→类型映射是业主待定的设计。
   若要一次到位，需先由业主给出复垦的「哪一章配哪类图」，再改 `chapter_image_specs`。
2. 复垦的中性规格 `max=4` / `min=0` 只是占位（不触发缺图提示）；真实配额随上面的映射一并定。
3. 相邻的稳评口径结构（`FOLDER_CHAPTER_HINTS`、`MATERIAL_IMAGE_RULES` 的 `rule_label`、
   `APPENDIX_IMAGE_CATEGORIES` 的附件1-10 名、`_pdf_page_rule`）**本次未改**。本 pass 靠
   「复垦图注一律取章标题」把它们的**标签**从正文图注里挡掉了；但它们**自身**仍是稳评口径
   （复垦当前不挂附件章，附件分类名暂不显形）。属同一个「复垦配图/附件」专项。

> 结论：复垦正文里**再不会出现任何稳评图注**（「社会稳定风险评估流程图」「拟征收…示意图」等）；
> 复垦图片图注一律为「复垦章标题+示意图」。**缺的是复垦自己的章→图规格，需业主定。**

---

## 3. 验证（stash-diff 实测，非阅读）

`/tmp/_stability_dump.py`：对若干正文文本取 `full_guardrail_check`（default 与 `domain="stability"`）
的整份 dict；对若干材料文件夹取 `build_image_catalog`（**默认域**，改动前后均可调用）的整份结果；
`json.dumps` 落盘。流程：工作树跑一遍 → `git stash`（回到 `63f8ced`）跑一遍 → `git stash pop` →
两版 diff。仅用 `tmp_path`（`tempfile.mkdtemp`），未触网、未碰真实库。

**结果（归一路径后逐行 diff）**：

- **Item 2（图片目录）**：两版**逐字节相同**（唯一差异是 mkdtemp 随机目录名，归一后为零）。
  稳评各图注原样：`位置图`→「项目位置示意图」、`评估流程`→「社会稳定风险评估流程图」、
  `公示照片4`→「公示照片」、`村民开会现场`→「群众座谈会照片」、`图片-稳评（报告中`→「网络舆情搜索截图」。
- **Item 1（护栏 dict）**：唯一实质差异是 `guardrail_hit` 一例的 `total_issues` **1 → 0**（即本次修复）；
  `plain`（3）与 `flow`（0）两版一致。两版都满足 `default == stability`（断言为真）。
  改动前后通用文本无差异 —— 与「稳评逐字节不变」一致，差异只出现在**故意逐字命中稳评护栏**的那一例上。

> 说明：Item 1 的修复点只在「稳评护栏串逐字出现在正文」时才可观测（真实报告几乎不会），
> 故 stash-diff 里对普通文本两版相同属**预期**；门控本身由新增用例（monkeypatch 强制命中）证明。

---

## 4. 全量测试

```
python3 -m pytest tests/ -q
→ 377 passed / 1 skipped / 5 failed
```

- 基线 `369 / 1 / 5` → **净增 8**（本次新增用例：`test_image_caption_domain.py` 7 个 +
  `test_guardrails_domain_grouping.py` 1 个），**失败数不变**。
- 5 个失败与基线完全一致（`date_source_rule` ×1 / `hitl_resume_loop` ×2 / `survey_fixes` ×2），
  均为本分支既存失败，**未追逐**。
- 新增用例全用 `tmp_path`（`tempfile.mkdtemp`），未触网、未碰真实库。

---

## 5. 自查

- **无第二份**：`CHAPTER_IMAGE_SPECS` 只在 `image_catalog` 定义一次（稳评分支委托它）；
  `_FALLBACK` 提为模块级 `IMAGE_CAPTION_FALLBACK` 后**只有一份**，局部副本已删。
- **无死代码**：`image_caption` / `chapter_image_specs` 的复垦分支**可达**（有实测用例）；
  `build_image_catalog` 内不再遗留任何指向稳评常量的直接引用（grep 已核）。
- **`image_catalog` 内无 `if domain == …`**：域差异全在 `chapter_catalog` 收口，本文件只调用 seam。
- **稳评默认**：`domain` 形参默认 `"stability"`；不传参的所有调用路径（含单章导出）逐字不变。
- **无新依赖**、未引入 GIS/几何库。
- **调用点全覆盖**：`report_workflow`、`report_assembler`、`gen_one_chapter.py`、`export_chapter.py`
  均按域传参（`export_chapter.py` 原先没钉 `_domain`，已补 —— 否则复垦单章导出会回落稳评图注）。

## 6. 未能做 / 存疑

- **复垦的章→图规格（绑定映射 + 配额）未做**：属业主待定设计，见 §2「仍未决定」。
- **`FOLDER_CHAPTER_HINTS` / `MATERIAL_IMAGE_RULES` / `APPENDIX_IMAGE_CATEGORIES` / `_pdf_page_rule`
  等仍是稳评口径**：本 pass 只把复垦**正文图注**统一成章标题派生，未改这些结构自身。
- 两个既存死方法（`_add_compilation_note` / `_add_legal_basis`）仍无人调用、含稳评字样 —— 不可达，
  不在本次「渲染内容」范围，保留。
