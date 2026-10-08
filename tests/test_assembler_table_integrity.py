"""组装期的**表格完整性**回归。

三处真实缺陷（2026-09-23 实测稿「洪拟征告〔2026〕10号…_0923_1112.docx」）：

① 章号解析表只写到「十」→ 第 11 章及以后一律回落到 0，
   表号重编与同章表格去重**整体静默失效**，第12章因此留下两张一样的测评表。
② 正文里「第11章识别的主要风险因素，经问卷调查…」也被当成章标题，
   章号被带偏到「第11章」。
③ 相邻两张 markdown 表之间没有正文/空行时被并成**一张** docx 表 ——
   第12章「措施前评分表 + 措施前后得分对比表」合成了一张 29 行的怪表，
   表中间冒出「序号|评估维度|措施前得分|措施后得分」这行新表头。
"""

import re

from docx import Document


# ════════════════════════════════════════════════════════
# ① 章号解析（含第 11 章及以后）
# ════════════════════════════════════════════════════════

def test_cn_num_to_int_covers_teens_and_beyond():
    from app.agent.chapter_definitions import cn_num_to_int

    cases = {"一": 1, "十": 10, "十一": 11, "十二": 12, "十四": 14,
             "二十": 20, "二十三": 23, "一百零五": 105,
             "7": 7, "14": 14}
    for s, want in cases.items():
        assert cn_num_to_int(s) == want, f"{s} → {cn_num_to_int(s)}，应为 {want}"


def test_chapter_no_of_heading_rejects_body_sentence():
    """正文里提到「第11章…」不是章标题，不得被当成章标题。"""
    from app.agent.chapter_definitions import chapter_no_of_heading as ch

    assert ch("第十一章、分析预测风险源") == 11
    assert ch("第十四章、附件") == 14
    assert ch("第十二章 确定风险等级") == 12
    # 🔴 实测的误匹配句（以「第11章」开头）
    assert ch("第11章识别的主要风险因素，经问卷调查、部门意见征询和实地核查，"
              "发生概率较小，影响程度较低，初始风险等级均评定为低风险。") == 0
    assert ch("表12-1 直接定性指标") == 0
    assert ch("（三）主要风险因素初始风险等级表") == 0


# ════════════════════════════════════════════════════════
# ③ 相邻两张 markdown 表必须拆成两张
# ════════════════════════════════════════════════════════

_TWO_TABLES_MD = """### （二）稳评风险等级确定量化指标评价体系

量化指标评价体系见下表。

| 测评指标 | 权重 | 测评项目 | 评分 |
|---|---|---|---|
| 合法性 | 10 | 事项实施主体是否符合规定 | 3 |
| 合计 | 10 | 小计 | 3 |
| 序号 | 评估维度 | 措施前得分 | 措施后得分 |
|---|---|---|---|
| 1 | 合法性 | 0 | 0 |
| 2 | 合计 | 0 | 0 |
"""


def _fresh_assembler():
    from app.services.report_assembler import ReportAssembler

    a = ReportAssembler()
    a._inserted_images = set()
    a._chapter_image_counter = {}
    a._system_rendered_tables = set()
    a._rendered_extracted_tbls = set()
    a._session_state = {}
    return a


def test_back_to_back_markdown_tables_are_not_merged():
    a = _fresh_assembler()
    d = Document()
    a._setup_page(d)
    a._add_chapter(d, 12, _TWO_TABLES_MD, {}, {}, [], {})

    assert len(d.tables) == 2, (
        f"相邻两表被并成了 {len(d.tables)} 张；表头依次为 "
        f"{[c.text.strip() for c in d.tables[0].rows[0].cells]}"
    )
    first = [c.text.strip() for c in d.tables[0].rows[0].cells]
    second = [c.text.strip() for c in d.tables[1].rows[0].cells]
    assert first[:2] == ["测评指标", "权重"]
    assert second[:2] == ["序号", "评估维度"], f"第二张表表头错了：{second}"
    assert len(d.tables[0].rows) == 3, "第一张表应只有表头 + 2 行数据"
    assert len(d.tables[1].rows) == 3, "第二张表应只有表头 + 2 行数据"


def test_chapter12_scoring_table_keeps_all_six_columns():
    """第12章的量化评分表必须是 6 列 —— 旧代码按章号硬裁成 4 列，
    把「评分标准」「得分」两列切掉了，与提示词要求直接冲突。"""
    a = _fresh_assembler()
    d = Document()
    a._setup_page(d)
    a._add_chapter(d, 12, """### （二）稳评风险等级确定量化指标评价体系

量化指标评价体系见下表。

| 测评指标 | 权重 | 测评项目 | 评分 | 评分标准 | 得分 |
|---|---|---|---|---|---|
| 合法性 | 10 | 事项实施主体是否符合规定 | 0 | 全部符合不计分，不符合计10分 | 0 |
| 合计 | 100 | 小计 | 0 |  | 0 |
""", {}, {}, [], {})

    assert len(d.tables) == 1
    hdr = [c.text.strip() for c in d.tables[0].rows[0].cells]
    assert hdr == ["测评指标", "权重", "测评项目", "评分", "评分标准", "得分"], (
        f"第12章评分表列被裁掉了：{hdr}"
    )


# ════════════════════════════════════════════════════════
# ① 同章重复表去重（第 11 章及以后也要生效）
# ════════════════════════════════════════════════════════

def _add_table(doc, headers, data_rows):
    t = doc.add_table(rows=len(data_rows) + 1, cols=len(headers))
    for i, h in enumerate(headers):
        t.rows[0].cells[i].text = h
    for ri, row in enumerate(data_rows, start=1):
        for ci, v in enumerate(row):
            t.rows[ri].cells[ci].text = v
    return t


def test_dedup_duplicate_tables_works_past_chapter_ten():
    """第十二章（>十）里两张一样的测评表必须去重，且连孤立表题一起删。

    🔴 真实稿里两张表**没有挨着** —— 中间夹着正文
       「第11章识别的主要风险因素，经问卷调查…」。旧写法把这句当成了章标题，
       把第二张表划到「第11章」，于是同章判定失效、重复表留了下来。
       这里就按真实顺序摆：表 → 那句正文 → 重复表。
    """
    a = _fresh_assembler()
    d = Document()
    d.add_paragraph("第十二章、确定风险等级")
    headers = ["测评指标", "权重", "测评项目", "评分"]
    rows = [["合法性", "10", "事项实施主体是否符合规定", "3"],
            ["合计", "10", "小计", "3"]]

    d.add_paragraph("表12-2 稳评风险等级确定量化指标评价体系（措施前）")
    _add_table(d, headers, rows)
    d.add_paragraph("第11章识别的主要风险因素，经问卷调查、部门意见征询和实地核查，"
                    "发生概率较小，影响程度较低，初始风险等级均评定为低风险。")
    d.add_paragraph("表11-1 稳评风险等级确定量化指标评价体系（措施前）")
    _add_table(d, headers, rows)

    assert len(d.tables) == 2
    a._dedup_duplicate_tables(d)

    assert len(d.tables) == 1, "同章重复的测评表没被去掉"
    titles = [p.text.strip() for p in d.paragraphs if p.text.strip().startswith("表")]
    assert titles == ["表12-2 稳评风险等级确定量化指标评价体系（措施前）"], (
        f"被删表的孤立表题没清掉：{titles}"
    )


def test_dedup_keeps_same_table_in_different_chapters():
    """同表头但**不同章**的表不得互删（措施前/措施后分别在第12、4章）。"""
    a = _fresh_assembler()
    d = Document()
    headers = ["测评指标", "权重", "测评项目", "评分"]
    rows = [["合法性", "10", "事项实施主体是否符合规定", "3"]]

    d.add_paragraph("第四章、社会稳定风险评估工作报告")
    _add_table(d, headers, rows)
    d.add_paragraph("第十二章、确定风险等级")
    _add_table(d, headers, rows)

    a._dedup_duplicate_tables(d)
    assert len(d.tables) == 2, "跨章的同名表被误删了"


def test_table_title_renumbering_works_past_chapter_ten():
    """第12章的表号必须重编成 表12-N，而不是保留 LLM 写错的「表11-1」。"""
    a = _fresh_assembler()
    d = Document()
    d.add_paragraph("第十二章、确定风险等级")
    d.add_paragraph("表11-1 稳评风险等级确定量化指标评价体系（措施前）")
    _add_table(d, ["a", "b"], [["1", "2"]])
    d.add_paragraph("表3-7 群众意见分析")
    _add_table(d, ["c", "d"], [["3", "4"]])

    a._dedup_table_titles(d)
    titles = [p.text.strip() for p in d.paragraphs if p.text.strip().startswith("表")]
    assert titles == ["表12-1 稳评风险等级确定量化指标评价体系（措施前）", "表12-2 群众意见分析"], titles
    assert not any(re.search(r"表11-1", p.text) for p in d.paragraphs)
