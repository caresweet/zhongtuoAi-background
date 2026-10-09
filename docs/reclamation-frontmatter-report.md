# 复垦前置件文案 —— 稳评字样收口报告

BASE: `59e3db3`（`feat/reclamation`）。本报告记录「报告前置件/固定抬头文案」
这一类泄漏的收口：把所有**由装配器按固定路径盖上去、却写着稳评**的中文字串
改由 `app.agent.chapter_catalog.front_matter_labels(domain)` 按域取。

---

## 1. 找到的所有稳评特写字串与归类

扫描范围：`app/services/report_assembler.py` 全部字符串字面量（用 tokenize
区分「代码字符串」与「注释/docstring」），并对渲染路径逐条判断可达性。

### 1.1 渲染内容（已修复）

| # | 位置 | 字串 | 渲染条件 | 处理 |
|---|------|------|----------|------|
| 1 | `report_assembler.py:2031` 封面落款 | `稳评责任主体：` | 有责任单位时 | 按域取 |
| 2 | `report_assembler.py:2032` 封面落款 | `稳评实施单位：` | 有实施单位时 | 按域取 |
| 3 | `report_assembler.py:3693` 资质证书页 | `社会稳定风险评估业务培训证书` | 勾选了持证人员且有其证书图时 | 按域取 |
| 4 | `report_assembler.py:4041` 公司简介 | `稳评工作组人员及分工情况` | **每份报告** | 按域取 |
| 5 | `report_assembler.py:4066` 公司简介兜底名单 | `稳评负责人` | 人员库为空时 | 按域取 |
| 6 | `report_assembler.py:4080` 公司简介末句 | `本报告相关机构营业执照、项目人员职称及稳评培训证书详见附件1。` | **每份报告** | 按域取（改写） |
| 7 | `report_assembler.py:4347` 地图类图片兜底图名 | `拟征收土地位置示意图` | 第6章有待插图且该图无图注时 | 按域取 |
| 8 | `report_assembler.py:5273` Word 批注作者 | `稳评审核Agent` | 有审核批注/缺口时（作者名在 Word 审阅窗格可见） | 按域取 |

第 7、8 项超出了「典型前置件」的范围，但同属「固定字串按某条路径盖进文档、
复垦报告照样出现」这一类，故一并收口。

### 1.2 内部标识符 / 不可达（保留）

| 位置 | 字串 | 归类 | 理由 |
|------|------|------|------|
| `report_assembler.py:442` | `稳评责任单位` | 内部字段名 | 读 `filled_data` 的 key，不渲染 |
| `report_assembler.py:618/620` | `（评审表生成失败…）` / 日志 | 稳评专有分支 | 仅在 `ch_num==3 and has_review_table`（稳评）内 |
| `report_assembler.py:1139-1143 / 1683-1687` | `作为本项目土地征收` 等 | **删除型**匹配 | 是「把含该措辞的段删掉」的模式，不注入 |
| `report_assembler.py:1289-1291 / 1422` | `拟征收土地位置` / `以正式征收公告为准` 等 | 评审表字段/遁词替换 | 只在**文中已存在**该稳评串时替换；复垦正文无此串 → 不触发 |
| `report_assembler.py:248-349` | `_ATTACHMENT_MAIN/_SUBS/_APPENDIX_SUB_ALIAS/_image_subtype` | 附件子系统 | `_add_appendices` 对复垦**不调用**（`appendix_chapter_no=None`） |
| `report_assembler.py:342` | `_PDF_DOC_LABELS` | 死代码 | 全仓无调用点 |
| `report_assembler.py:3462/3470` | `征地范围图/征收示意图/征收公告` | 图片尺寸/分类匹配 | 匹配既有图注，不生成字串 |
| `report_assembler.py:3809` | `稳评`（证书名匹配） | 匹配器 | 用于识别人员库里「稳评培训证书」，不渲染 |
| `report_assembler.py:4861` | `稳评平台备案` | 稳评分支 | 在 `if domain == "stability"` 内 |
| `report_assembler.py:5003` | `征收` | 图片分类关键词 | 匹配器 |
| `report_assembler.py:3850-3858` | `受…委托…社会稳定风险评估…` | **死方法** `_add_compilation_note` | 全仓无调用点；见 §6 |
| `report_assembler.py:4102-4118` | DB32/征地/社会稳定风险评估 法规清单 | **死方法** `_add_legal_basis` | 全仓无调用点；见 §6 |

---

## 2. 修复方式

新增 `app/agent/chapter_catalog.py:145  front_matter_labels(domain) -> Dict[str,str]`，
是本节所有固定文案的**唯一收口点**；装配器不再写死，也不在此处按域分叉。

| key | 复垦取值 | 稳评取值（逐字等于改动前） |
|-----|----------|------------------------------|
| `org_label` | `复垦责任主体：` | `稳评责任主体：` |
| `implement_label` | `复垦实施单位：` | `稳评实施单位：` |
| `roster_heading` | `复垦方案编制人员及分工情况` | `稳评工作组人员及分工情况` |
| `roster_lead_role` | `复垦方案负责人` | `稳评负责人` |
| `cert_section_heading` | `土地复垦业务培训证书` | `社会稳定风险评估业务培训证书` |
| `material_note` | `本报告相关机构营业执照、项目人员职称及培训证书等材料详见公司资质证书。` | `本报告相关机构营业执照、项目人员职称及稳评培训证书详见附件1。` |
| `map_caption` | `位置示意图` | `拟征收土地位置示意图` |
| `audit_comment_author` | `复垦审核Agent` | `稳评审核Agent` |

默认域（stability / None）**逐字等于**改动前装配器里写死的那几处。

### 培训证书 / 附件1 那句的处理与理由（任务第 4 条要求点名）

- **改写了，没有停用。** 该句（`material_note`）在 `_add_company_intro` 结尾
  **无条件渲染**（不依赖任何数据），符合任务给出的「renders into every report → 改写」判据。
- 原句的「详见附件1」在复垦里**指向不存在的章**（`appendix_chapter_no("reclamation")=None`），
  必须一并去掉。复垦改写为指向前置的「公司资质证书」节（`_add_company_qualifications`
  渲染的小节名就是「公司资质证书」）。这是判断，非稳定口径；已按任务要求交由业主复核措辞。

---

## 3. 校验（stash-diff，不是目测）

方法：固定 state（含 `implement_unit`，触发封面落款）跑一遍装配，导出
段落 + 表格 + 页眉页脚文本；分别在**工作区**与 **`git stash` 回到 base 后**各取一份，
逐行 diff。

- **稳评：逐行 IDENTICAL**（含封面落款、工作组标题、公司简介全文、页眉、文件名）。
- **复垦：仅出现 4 处预期变化**，其余全等：

```
- 稳评实施单位：某某复垦公司
+ 复垦实施单位：某某复垦公司
- 稳评工作组人员及分工情况
+ 复垦方案编制人员及分工情况
- 稳评负责人
+ 复垦方案负责人
- 本报告相关机构营业执照、项目人员职称及稳评培训证书详见附件1。
+ 本报告相关机构营业执照、项目人员职称及培训证书等材料详见公司资质证书。
```

- **复垦渲染文本的禁用串计数全为 0**：
  `稳评=0 社会稳定风险评估=0 评审表=0 DB32=0 征地=0 征收=0 拟征收=0`
  （稳评渲染文本同一脚本计数：`稳评=48 社会稳定风险评估=23`，未变）。

---

## 4. 测试

- 新增 3 例于 `tests/test_reclamation_domain_dispatch.py`（E 节）：
  - `test_front_matter_labels_per_domain` —— 钉住两域标签解析；稳评逐字 +
    复垦不含任何稳评标记。
  - `test_reclamation_render_front_matter_has_no_stability_wording` —— 端到端渲染，
    复垦正文禁用串为 0，且复垦口径文案确实在。
  - `test_stability_render_front_matter_labels_unchanged` —— 稳评前置件文案逐字不变。
- 全部用 `tmp_path`，未触网、未碰真实库。

**全量结果**：`369 passed / 1 skipped / 5 failed`。
基线 `366 passed / 1 skipped / 5 failed` —— 净增 3（本次新用例），
5 个失败与基线完全一致（survey×2 / date / HITL×2），未追逐。

---

## 5. 自查

- 未复制任何 catalog 已拥有的字串：8 个字串从装配器**搬**到 catalog，装配器只 read。
- 未新增死代码（`front_matter_labels` 8 个 key 全部被装配器使用）。
- 未引入新依赖。
- stability 分支直接返回字面量（不委托别的模块，因为原本就是这里的字面量）。
- 复核了「渲染 vs 删除 vs 匹配 vs 内部名」四类，见 §1.2。

## 6. 未能收口 / 存疑（需业主裁决）

1. **`app/services/image_catalog.py` 的图片→图注映射仍是稳评口径**（**未改**）。
   `CHAPTER_IMAGE_SPECS`（`image_catalog.py:39`，按稳评章号 6/7/8/9 挂标签，
   含「社会稳定风险评估流程图」）与 `_FALLBACK`（`image_catalog.py:443`，
   含「拟征收地块位置示意图」「拟征收土地位置示意图（公告附图）」）由
   `build_image_catalog` 产出，经 `_prepare_image_catalog` 变成 `_chapter_image_map`
   并渲染为**正文图注**。**只要复垦报告带了被分类到这些类别的图片，就会印出稳评图注**。
   这不是「前置件文案」，且复垦的「章号→图片类型」是一个**需要设计的映射**
   （复垦第 6 章是「工程设计」而非「项目概况」），**没有可推断的等价口径**，
   故按任务要求停手上报，不臆造。
2. **兜底人员名单是本公司稳评团队**（`report_assembler.py:4066-4079`，陈春…罗娜）。
   人员库为空时整块渲染，外部用户出复垦报告会带上我们的人。这是**公司隔离**问题
   （非域问题，稳评同样存在），且要改需先决定复垦该显示谁 —— 未改。
3. **两个死方法**：`_add_compilation_note`（编制说明）与 `_add_legal_basis`
   （法律法规依据）在 `59e3db3` 之前就已无人调用（装配流水线注释见
   `report_assembler.py:566`），其中含 `社会稳定风险评估`/`DB32`/`稳评培训证书`。
   **不可达，故不渲染**，不属于本次「渲染内容」类；按任务「可保留既有无关死代码」
   口径**保留**，但点名提示：若日后重新接上，需先按域取文案。
