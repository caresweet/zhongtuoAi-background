# 复垦「章 → 图」配图规格 — 实现报告

分支 `feat/reclamation`，BASE `ebdc8ac`。改动三处 + 一处测试：
`app/agent/reclamation_definitions.py`（新增真规格）、`app/agent/chapter_catalog.py`
（收口：规格/图注/绑定）、`app/services/image_catalog.py`（改走 seam）、
`tests/test_image_caption_domain.py`（按新规格重写复垦用例）。

---

## 1. 建出的规格（`RECLAMATION_IMAGE_SPECS`）

放在 `app/agent/reclamation_definitions.py`（复垦章节结构的唯一事实源，与稳评
`image_catalog.CHAPTER_IMAGE_SPECS` 同形、互不复用）。`{章号: [{type,label,min,max,keywords}]}`。

| 章 | type | label（取样本原题） | min | max | 依据样本 |
|---|---|---|---|---|---|
| 1 前言 | `area_chart` | 各类土地面积关系图 | 0 | 1 | ⚠️ **弱证据 1/5**：仅「污水处理厂」图1-1 |
| 3 项目概况 | `location_map` | 项目地理位置示意图 | 0 | 2 | **强证据 5/5**：井神/王元佳苑/污水处理厂/宁淮城际/明远路 |
| 4 复垦方向可行性分析 | `site_photo` | 临时用地现场照片 | 0 | 4 | **强证据 5/5**（损毁分析类） |
| 4 | `occupancy_map` | 临时用地占用永久基本农田示意图 | 0 | 3 | 井神 + 污水处理厂（占用情况类） |
| 4 | `measure_layout` | 表土堆放区位置示意图 | 0 | 2 | 仅井神（措施类） |
| 5 质量要求与复垦措施 | `section` | 临时用地硬化层断面示意图 | 0 | 1 | ⚠️ **弱证据 1/5**：仅「污水处理厂」图5-1 |

**keywords（供按名绑定）**：
- ch3：`位置示意图 / 地理位置 / 工程位置 / 项目位置 / 位置图 / 区位 / 收水范围`
  （覆盖样本里的 项目地理位置 / 项目位置 / 工程位置 / 王元佳苑位置 / 收水范围 五种写法，取
  「项目地理位置示意图」作正则名）
- ch4 损毁分析：`现场照片 / 现场影像 / 影像资料 / 影像图 / 现状照片 / 实地照片 / 主体工程 /
  位置关系 / 平面布置 / 布置图 / 位置变化 / 拌合站`
- ch4 占用情况：`永久基本农田 / 基本农田 / 生态红线 / 生态管控区 / 占用 / 三区三线`
  （收「占用」二字即覆盖样本里「占用…」与「**不**占用…」两种写法）
- ch4 措施：`表土堆放 / 堆放区 / 表土 / 涵管 / 过渠道`
- ch1：`土地面积关系 / 面积关系图 / 面积构成 / 地类面积`
- ch5：`硬化层 / 断面 / 铺装断面 / 结构断面`

### 取舍说明（min / 缺省章）
- **`min` 一律 0，包括 ch3。** ch3 的位置示意图虽 5/5 出现，但复垦配图**由业主上传**：
  样本证明「通常会有」，不等于「缺了就判必须补」。给 `min:0` 只用 `max` 限张数，避免凭空
  催图。样本里没有任何一章的图是「系统必然自带、缺则报错」的，故不设 `min:1`。
- **ch2/6/7/8/9/10 不设条目**（5 份样本全无配图）。选择「缺省」而非「空列表」：`chapter_image_specs`
  缺省章号 = 该章无正文图，与稳评口径一致（稳评那表也只列 6/7/8/9）；空列表会多出一层
  「有规格但为空」的语义，无调用点消费。业主后续补图时按同形状追加即可。

---

## 2. 绑定现在怎么走（域感知）

原来「图片 → 章」经 `image_catalog` 里稳评校准的结构（`FOLDER_CHAPTER_HINTS` /
`MATERIAL_IMAGE_RULES` / `_pdf_page_rule`，写死稳评 6/7/8/9 章）。**问题**：复垦图会被这些
规则错绑到复垦同号章（如「位置示意图」→ 稳评第6章 → 复垦第6章「工程设计」，错）。

改法：把两处调用点改成 `chapter_catalog` 的 seam 函数，域差异在 seam 里分派，`image_catalog`
里**没有任何 `if domain == ...`**：

- `material_image_rule(domain, name, folder_hint, pdf_page)` — ① 确定性材料规则。
  稳评：原样委托 `_pdf_page_rule` / `_match_material_rule`。复垦：`_reclamation_material_rule`
  **扫 `RECLAMATION_IMAGE_SPECS` 的关键词**，命中返回 `(复垦章号, 规格type, 规格图名)`。
- `image_chapter_hint(domain, name, folder_hint)` — ② 规则未命中时的兜底提示。
  稳评：原样委托 `_classify_chapter_hint`。复垦：恒返回 `None`（绝不借稳评那套写死章号的
  文件夹提示；复垦的判定已在 ① 用关键词做完）。

**扫描顺序（关键）**：ch3 的泛名『位置示意图』会被 ch4『表土堆放区位置示意图』里的同名片段
误命中，故 `_reclamation_hint_chapters()` 把 **ch3 排到最后扫**——专名章先落袋、泛名章兜底。
其余章关键词互不重叠，顺序无关；新增章自动纳入（按章号升序，ch3 恒居末）。

**图注**（`image_caption` 复垦支）：命中规格 → `spec_label`（如「项目地理位置示意图」）；
未命中 → 退回「章标题 + 示意图」中性兜底（如「项目概况示意图」）。稳评支逐字未动。

**第二遍「类别竞争」对复垦天然失效**：复垦规格的 `type`（`location_map`/`site_photo`/
`occupancy_map`/`measure_layout`/`area_chart`/`section`）不与分类器输出的通用类别
（`map`/`photo`/…）相撞，故无关键词命中的复垦图**只进附件、不进正文**——复垦无附件章，
即不被错置。这是**刻意**的确定性选择：复垦绑定只认名字/文件夹，不靠类别猜。（样本里每张图
都有描述性名称，够用。）

---

## 3. 稳评保稳验证（stash-diff，非目测）

`/tmp/stab_snapshot.py` 对默认域（stability）跑一份全量行为快照：`chapter_image_specs(None)`
全表、22 种代表性文件夹名经 `build_image_catalog` 的 `by_chapter` 图注 / `missing` /
`appendix` / `catalog`（含 category、display_name、chapter_hint、pinned）、若干 `image_caption`
取值、以及 `domain` 省略 vs 显式 `stability` 的相等性。

- 工作树（改动后）→ `/tmp/stab_after.txt`
- `git stash` → `ebdc8ac` 干净树 → `/tmp/stab_before.txt`
- `git stash pop` 还原

```
$ diff /tmp/stab_before.txt /tmp/stab_after.txt && echo BYTE-IDENTICAL
STABILITY SNAPSHOT: BYTE-IDENTICAL (ebdc8ac vs working tree)
```

稳评路径逐字节不变。

---

## 4. 全量测试 vs 基线

`cd backend && python3 -m pytest tests/ -v`

- 基线：`377 passed / 1 skipped / 5 failed`
- 本次：`380 passed / 1 skipped / 5 failed`

失败 5 项与基线**同一批**（`test_survey_fixes` ×2、`test_date_source_rule` ×1、
`test_hitl_resume_loop` ×2）——即任务声明「pre-date 本分支、勿追」的那 5 个，未触碰。
通过数 +3 = 重写后的复垦配图用例（旧 7 个 → 新 10 个）。

---

## 5. 刻意留给中性兜底的部分

- **ch2/6/7/8/9/10 无规格** → `chapter_image_specs` 缺省该章 → 该章无正文图。样本无图，不臆造。
- **未命中任何关键词的复垦图** → 不进正文（也无附件章），既不错绑也不给错图注。理由见 §2。
- **ch1/ch5 弱证据** → 建了 `min:0,max:1` 条目并在注释里标注「仅 1/5 样本」。给名目但不强制。
- **`image_caption` 复垦支的 `rule_label`** 保留在 `spec_label or rule_label or …`：关键词绑定下二者
  恒等（`material_image_rule` 返回的类别即规格 type → `matched` 必命中 → `spec_label` 即该图名）；
  保留 `rule_label` 作二次兜底，非死代码（`image_chapter_hint` 恒 None 的场景下仍可能只给 rule_label）。

## 6. 自审发现

- 删除死件：`chapter_catalog` 里旧的中性兜底常量 `_NEUTRAL_IMAGE_TYPE` / `_NEUTRAL_IMAGE_MAX`
  及其内联构造已删，被真规格取代；`image_catalog` 里 `chapter_image_specs`/`image_caption`
  的旧直呼 import 改为经 `_catalog` 模块调用，无第二份实现。
- 未引入新依赖、无 GIS/几何库。
- `test_image_caption_domain.py` 里 `_img_in` 追加文件夹名进图片字节，避免多图用例被
  `_image_fingerprint` 内容去重误杀（旧用例单图未暴露）。
- 已知、**未改**（超出本任务seam范围）：`report_assembler` 的正文图配额 `_BODY_QUOTA` 是按稳评
  类别名给的，复垦 type 落到 `_DEFAULT_BODY_QUOTA=2`，即每类至多 2 张进正文——与 max 不冲突，
  行为合理，留待业主定复垦配额口径时再收口。
