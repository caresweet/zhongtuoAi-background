"""KnowledgeAgent — 知识库模板/规范/格式检索专家Agent

职责：
1. 从知识库检索与当前章节相关的模板结构、标准格式、规范要求
2. 提供「格式模板」：每章应有的标题层级、表格结构、固定段落
3. 提供「写作范例」：从历史报告中提取对应章节的范文片段
4. 提供「法规依据」：DB32/T4013-2021等规范中对应章节的条文
5. 缓存检索结果，避免重复查询

在章节生成前被 ChapterOrchestrator 调用，为 ChapterAgent 提供知识库上下文。
"""

import asyncio
import logging
import re
from typing import Dict, List, Any, Optional

from .base_agent import BaseAgent
from app.agent.chapter_definitions import ALL_CHAPTERS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 每章知识库检索模板
# ═══════════════════════════════════════════════════════════════

CHAPTER_KB_QUERIES: Dict[int, Dict[str, Any]] = {
    2: {
        "structure": "第2章 编制说明 评估背景 评估目的 评估原则 编制依据",
        "format": "编制说明格式 稳评工作背景 评估目的说明",
        "regulation": "DB32/T4013-2021 稳评编制说明要求",
    },
    3: {
        "structure": "第1章 稳评评审报告表 决策事项名称 稳评责任单位 稳评实施单位 决策概况 利益相关者",
        "format": "稳评评审表 决策名称 责任单位 实施单位 决策概况 法律政策依据 利益相关者情况",
        "regulation": "社会稳定风险评估评审表 江苏省稳评规范化运作统一制式",
    },
    4: {
        "structure": "第9章 提出防范和化解风险的工作建议 宣传 补偿 资金 社保 信访 措施汇总",
        "format": "风险防范与化解措施汇总表 序号 风险类型 防范措施 责任主体 完成时限",
        "regulation": "风险防范措施 化解措施 责任主体 宣传公示 补偿方案 资金监管",
    },
    5: {
        "structure": "第10章 稳评工作报告 风险等级量化评分 措施前评分 措施后评分 对比 结论与建议",
        "format": "量化评分表 措施前后对比表 评估结论 风险等级判定",
        "regulation": "DB32/T4013-2021 风险等级量化 评分标准 低风险 中风险 高风险 评估结论",
    },
    7: {
        "structure": "第4章 决策事项基本情况 决策名称 相关单位 地理位置 征收范围面积 资金筹措 实施周期",
        "format": "拟征收土地基本情况表 表头 数据行",
        "regulation": "土地管理法 征收公告 预公告 征地程序",
    },
    9: {
        "structure": "第3章 编制依据 法律法规 标准规范 项目文件 评估依据法规清单",
        "format": "评估依据法规清单 法规名称 文号 编号列表格式",
        "regulation": "土地管理法 DB32/T4013-2021 征收条例 地方规范",
    },
    11: {
        "structure": "第5章 稳评工作方案 评估流程 评估方法 工作步骤 时间安排 人员分工",
        "format": "稳评工作组人员及分工情况 评估流程图",
        "regulation": "DB32/T4013-2021 评估程序 评估方法 稳评工作规范",
    },
    12: {
        "structure": "第6章 各利益相关者对决策事项的反映 公众意见 基层组织意见 问卷调查 座谈走访 网络舆情",
        "format": "公众意见调查分析表 部门意见调查分析表 调查内容 选项 人数 比例",
        "regulation": "问卷调查 公众参与 座谈会 利益相关者意见",
        "structure": "第8章 分析预测风险源 风险因素识别 对照表法 实地考察 问卷法 风险等级判定",
        "format": "风险因素初始风险等级表 序号 风险类型 风险因素描述 风险等级",
        "regulation": "补偿方案风险 资金分配风险 社保名单风险 信访舆情风险",
    },
    13: {
        "structure": "第7章 决策事项合法性合理性可行性可控性分析 合法性分析 合理性分析 可行性分析 可控性分析",
        "format": "四性分析 主体合法 程序合规 补偿合理 群众接受",
        "regulation": "征收主体合法性 规划相符性 程序合规性 补偿合理性",
    },
    14: {
        "structure": "第11章 应急预案 编制目的 依据 范围 指导思想 工作原则 组织领导 预警预防 现场处置",
        "format": "应急组织体系表 应急指挥部 职责分工 预警机制 应急响应",
        "regulation": "突发事件应对 应急预案 预警机制 应急响应 舆情处置",
        "structure": "第12章 附件 公示照片 勘察照片 座谈会照片 调查问卷 专家评审",
        "format": "附件列表 照片图注",
        "regulation": "附件材料要求",
    },
}

# 每章应有的固定结构定义（企业标准模板）
CHAPTER_STRUCTURE_SPECS: Dict[int, Dict[str, Any]] = {
    2: {
        "sections": ["2.1 评估背景", "2.2 评估目的", "2.3 评估原则"],
        "required_tables": [],
        "fixed_content": [],
        "min_words": 300, "max_words": 1000,
        "sections": ["2.1 评估过程", "2.1.1 评估过程5步骤", "2.1.2 评估流程图",
                      "2.1.3 实施过程", "2.1.4 评估方法", "2.2 评估依据"],
        "required_tables": ["评估依据法规清单"],
        "fixed_content": ["19项法律法规依据（从知识库提取）"],
        "min_words": 300, "max_words": 2000,
    },
    3: {
        "sections": ["1.1 决策事项名称", "1.2 稳评责任单位", "1.3 稳评实施单位",
                      "1.4 决策概况", "1.5 法律政策依据及必要性",
                      "1.6 利益相关者情况", "1.7 各方意见", "1.8 主要风险因素",
                      "1.9 降低风险措施", "1.10 稳评结论"],
        "required_tables": ["稳评评审表"],
        "fixed_content": ["填表说明", "稳评实施单位（取本报告所属公司）"],
        "min_words": 500, "max_words": 2000,
    },
    4: {
        "sections": ["9.1 评估结论（四性总结）", "9.2 社会稳定等级判定",
                      "9.3 实施建议（4-5条）"],
        "required_tables": [],
        "fixed_content": [],
        "min_words": 400, "max_words": 2000,
    },
    5: {
        "sections": ["10.1 组织指挥体系", "10.2 分级响应", "10.3 处置措施"],
        "required_tables": [],  # 金湖模板第10章无表格
        "fixed_content": ["应急预案三段式结构"],
        "min_words": 200, "max_words": 800,
    },
    7: {
        "sections": ["4.1 决策名称", "4.2 决策相关单位", "4.3 决策地理位置",
                      "4.4 征收范围面积及地上附着物", "4.5 资金筹措", "4.6 决策实施周期"],
        "required_tables": ["拟征收土地基本情况表"],
        "fixed_content": ["稳评实施单位（取本报告所属公司）"],
        "min_words": 500, "max_words": 2000,
        "sections": ["4.1 合法性分析（4小节）", "4.2 合理性分析（3小节）",
                      "4.3 可行性分析（3小节）", "4.4 可控性分析（4小节）"],
        "required_tables": [],
        "fixed_content": [],
        "min_words": 600, "max_words": 3000,
    },
    9: {
        "sections": ["3.1 法律法规", "3.2 标准规范", "3.3 项目文件依据"],
        "required_tables": [],
        "fixed_content": ["19项法律法规依据（编号列表，不用表格）"],
        "min_words": 800, "max_words": 2500,
        "sections": ["3.1 调查方法", "3.2 调查范围", "3.3 调查内容", "3.4 调查过程（4阶段）",
                      "3.5 利益相关者意见", "3.5.1 公众意见", "3.5.2 基层组织意见",
                      "3.5.3 村民意见", "3.5.4 网络舆情", "3.6 诉求"],
        "required_tables": ["公众意见调查分析表", "部门意见调查分析表"],
        "fixed_content": [],
        "min_words": 400, "max_words": 2500,
    },
    11: {
        "sections": ["5.1 识别方法（对照表法/实地考察/问卷法）", "5.2 风险因素详述（4类）",
                      "5.2.1 补偿方案风险", "5.2.2 资金分配风险",
                      "5.2.3 社保名单风险", "5.2.4 信访舆论风险"],
        "required_tables": ["风险因素初始风险等级表"],
        "fixed_content": [],
        "min_words": 300, "max_words": 2000,
    },
    12: {
        "sections": ["6.1 量化指标体系", "6.2 合法性打分", "6.3 合理性打分",
                      "6.4 可行性打分", "6.5 可控性打分", "6.6 总分与等级判定"],
        "required_tables": ["措施前风险等级量化评分表"],
        "fixed_content": ["DB32/T4013-2021评分标准"],
        "min_words": 300, "max_words": 2000,
        "sections": ["8.1 措施后重新评分", "8.2 等级判定", "8.3 措施前后对比"],
        "required_tables": ["措施后风险等级量化评分表", "措施前后得分对比表"],
        "fixed_content": [],
        "min_words": 800, "max_words": 2500,
    },
    13: {
        "sections": ["7.1 总体防范措施", "7.2 具体防范措施", "7.2.1 加强宣传",
                      "7.2.2 补偿方案", "7.2.3 资金分配", "7.2.4 社会保障", "7.2.5 信访舆论"],
        "required_tables": ["风险防范与化解措施汇总表"],
        "fixed_content": [],
        "min_words": 500, "max_words": 2500,
    },
}

# ── 模板10张表格 → 用户资料数据源映射 ──
# 每张表定义：模板列名、数据来源（用户资料）、提取方式、禁止从模板复制
TEMPLATE_TABLE_DATA_RULES: Dict[int, Dict[str, Any]] = {
    2: {
        "评估依据法规清单": {
            "format": "bullet_list",  # NOT a table — use bullet list like the template
            "example_format": (
                "本章法规依据如下（请直接列出，不要用表格）：\n\n"
                "1. 《中华人民共和国土地管理法》（2019年修正，主席令第32号）\n"
                "2. 《中华人民共和国土地管理法实施条例》（2021年修订，国务院令第743号）\n"
                "3. 《国有土地上房屋征收与补偿条例》（国务院令第590号）\n"
                "4. DB32/T4013-2021《第三方社会稳定风险评估规范》（江苏省市场监督管理局）\n"
                "...\n\n"
                "每项法规注明完整名称、文号和发布单位。"
            ),
            "data_source": "📚 RAG知识库 (db32_t4013_2021.md, land_management_law.md等)",
            "rule": "⚠️ 不要用表格！用编号列表。法规名称、文号必须来自RAG检索结果。",
        },
    },
    3: {
        "拟征收土地基本情况表": {
            "template_cols": ["序号", "地块号", "土地坐落", "土地用途", "总面积(㎡)", "界址点数"],
            "data_source": "📄 勘测定界报告 PDF (0-勘测定界报告-.pdf)",
            "extraction_method": "pdfplumber.extract_tables() → 第1页表格",
            "extracted_fields": ["序号", "地块号", "土地坐落", "土地用途", "总面积", "界址点数"],
            "rule": "⚠️ 本表数据必须从PDF提取的表格数据中直接获取，不得从模板复制，不得编造",
        },
    },
    5: {},  # 金湖模板第10章无表格,
    9: {
        "公众意见调查分析表": {
            "template_cols": ["调查内容", "选项", "人数", "比例"],
            "example_table": (
                "请生成以下格式的4列表格(必须4列，至少5行数据)：\n\n"
                "| 调查内容 | 选项 | 人数 | 比例 |\n"
                "| --- | --- | --- | --- |\n"
                "| 项目知晓度 | 知道 | XX | XX% |\n"
                "| 项目知晓度 | 不知道 | XX | XX% |\n"
                "| 项目支持度 | 支持 | XX | XX% |\n"
                "| 项目支持度 | 条件支持 | XX | XX% |\n"
                "| 项目支持度 | 反对 | XX | XX% |\n"
                "| 主要关切问题 | 补偿标准 | XX | XX% |\n"
                "| 主要关切问题 | 安置方式 | XX | XX% |\n"
                "| 主要关切问题 | 社保问题 | XX | XX% |\n"
                "\n从用户资料中提取真实数据填充XX。没有数据的标注【待统计】。"
            ),
            "data_source": "📄 座谈会PDF + 🖼️ 问卷扫描图",
            "rule": "⚠️ 必须用4列格式！数据从用户资料提取，没有的标【待统计】",
        },
        "部门意见调查分析表": {
            "template_cols": ["调查内容", "选项", "个数", "比例"],
            "example_table": (
                "| 调查内容 | 选项 | 个数 | 比例 |\n"
                "| --- | --- | --- | --- |\n"
                "| 对决策的了解程度 | 了解 | X | X% |\n"
                "| 对决策的了解程度 | 基本了解 | X | X% |\n"
                "| 对决策的态度 | 支持 | X | X% |\n"
                "| 对决策的态度 | 有条件支持 | X | X% |\n"
                "\n如果用户没有提供部门调查数据，直接写【未开展部门调查】。"
            ),
            "data_source": "🖼️ 部门调查问卷扫描图",
            "rule": "⚠️ 必须4列！无数据标注【未开展部门调查】",
        },
    },
    11: {
        "风险因素初始风险等级表": {
            "template_cols": ["序号", "风险类型", "风险因素描述", "发生概率", "影响程度", "初始风险等级"],
            "data_source": "📊 第四章决策综合分析结论 + 📚 DB32/T4013-2021风险等级标准",
            "extraction_method": "从Ch4分析结论推导风险因素 → 参照DB32标准判定等级",
            "rule": "⚠️ 风险因素从Ch4分析结果中提取，风险等级按DB32标准判定，不得随意编造",
        },
    },
    12: {
        "措施前风险等级量化评分表": {
            "template_cols": ["一级指标", "二级指标", "满分", "得分", "扣分原因"],
            "data_source": "📊 第四章合法性/合理性/可行性/可控性分析 + 📚 DB32评分标准",
            "extraction_method": "从Ch4四性分析结果量化打分，参照DB32评分指标体系",
            "rule": "⚠️ 得分必须有依据，扣分原因引用Ch4分析中的具体问题",
        },
        "措施后风险等级量化评分表": {
            "template_cols": ["一级指标", "二级指标", "满分", "措施后得分", "得分变化"],
            "data_source": "📊 Ch6措施前评分 + Ch7措施效果 → 重新评估",
            "extraction_method": "在Ch6评分基础上，根据Ch7措施效果调整每项得分",
            "rule": "⚠️ 措施后得分必须比措施前有所改善（得分降低），变化幅度合理",
        },
        "措施前后得分对比表": {
            "template_cols": ["序号", "评估维度", "措施前得分", "措施后得分", "变化幅度"],
            "data_source": "📊 Ch6 + Ch8 评分结果对比",
            "extraction_method": "直接汇总Ch6和Ch8的评分数据",
            "rule": "⚠️ 本表数据必须与Ch6、Ch8中的评分完全一致",
        },
    },
    13: {
        "风险防范与化解措施汇总表": {
            "template_cols": ["序号", "风险类型", "防范化解措施", "责任主体", "完成时限"],
            "data_source": "📊 第五章风险识别结果 → 逐条制定对应措施",
            "extraction_method": "Ch5识别的每条风险 → 至少一条对应措施，责任主体从项目数据获取",
            "rule": "⚠️ 措施必须与Ch5的风险一一对应，责任主体从filled_data获取",
        },
    },
}


# ═══════════════════════════════════════════════════════════════
# 模板表格骨架 — 从洞庭湖稳评模板提取的完整表格结构
#
# 每张表定义：
#   - title: 表格标题
#   - columns: 列名列表
#   - fill_types: 每列的填充类型 (fixed=保留模板, data=填入项目数据, structural=序号/标签)
#   - fixed_rows: 固定内容行（模板中不变的部分，AI必须照抄这些内容）
#   - data_slots: 需要填入项目数据的列
#   - rule: 生成约束
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 🔴 第9章问卷统计表骨架 —— 由 questionnaire_parser 的**实际题目表**派生
#
# 旧版在这里手写死了「项目知晓度/项目支持度/补偿标准满意度/社保安置满意度/主要关切问题」
# 等题目，而《稳评问卷调查表》根本没问过满意度和关切问题 —— 模型只能整表填
# 【待统计】（实测第9章出现 22 处占位）。题目只有一个事实源：questionnaire_parser。
# ═══════════════════════════════════════════════════════════════

# 群众问卷题目键 → 表中「调查内容」列的中文表述
# 🔴 唯一事实源 = questionnaire_parser.PUBLIC_QUESTION_TEXTS（照抄《稳评问卷调查表》
#    表格第一列原文，逐字一致，不得概括改写 —— 业主评审要求与实际问卷对得上）。
def _public_q_titles() -> Dict[str, str]:
    try:
        from app.services.questionnaire_parser import PUBLIC_QUESTION_TEXTS
        return dict(PUBLIC_QUESTION_TEXTS)
    except Exception:
        return {
            "identity":   "您是",
            "age":        "您的年龄",
            "occupation": "您的职业",
            "awareness":  "您是否了解本决策",
            "attitude":   "对该决策是否支持",
            "resolution": "您会采取何种方式解决诉求",
        }

# 部门问卷题目键 → 中文表述（concerns 为多选）
_DEPT_Q_TITLES = {
    "awareness":              "对决策的了解程度",
    "publicity_satisfaction": "对宣传公示工作的满意度",
    "policy_understanding":   "对补偿安置政策的了解程度",
    "concerns":               "关心的主要事项",
    "risk_assessment":        "对社会稳定风险等级的判断",
    "stability_confidence":   "对保持社会稳定的信心",
    "support_attitude":       "对决策的基本态度",
}


def _rows_for(questions: dict, titles: dict, unit: str) -> list:
    """把 {题目键: [选项...]} 展开成表格固定行。"""
    rows = []
    for key, opts in questions.items():
        title = titles.get(key)
        if not title:
            continue
        for opt in opts:
            rows.append([title, opt, f"【填入{unit}】", "【填入比例】"])
    return rows


def _build_survey_skeletons() -> Dict[str, Any]:
    """第9章两张问卷统计表 —— 行来自问卷**实际题目**，不手写。"""
    try:
        from app.services.questionnaire_parser import PUBLIC_QUESTIONS, DEPT_QUESTIONS
    except Exception:
        return {}

    return {
        "公众意见调查分析表": {
            "title": "表9-1 个人问卷统计汇总表",
            "columns": ["调查内容", "选项", "人数", "比例"],
            "fill_types": {"调查内容": "fixed", "选项": "fixed",
                           "人数": "data", "比例": "data"},
            "fixed_rows": _rows_for(PUBLIC_QUESTIONS, _public_q_titles(), "人数"),
            "rule": (
                "🔒 调查内容与选项**照抄本表**（来自问卷实际勾选题目，不得增删题目）。\n"
                "✏️ 人数与比例取事实卡「问卷调查分项统计」，**逐字照抄，不得对调、不得估算**。\n"
                "⚠️ 问卷里没有的题目（如补偿标准满意度、安置方式满意度）**绝对不要出现**。"
            ),
        },
        "部门意见调查分析表": {
            "title": "表9-2 单位（部门）问卷统计汇总表",
            "columns": ["调查内容", "选项", "个数", "比例"],
            "fill_types": {"调查内容": "fixed", "选项": "fixed",
                           "个数": "data", "比例": "data"},
            "fixed_rows": _rows_for(DEPT_QUESTIONS, _DEPT_Q_TITLES, "个数"),
            "rule": (
                "🔒 调查内容与选项**照抄本表**（来自部门问卷实际题目）。\n"
                "✏️ 个数与比例取事实卡「部门（单位）问卷调查」，逐字照抄。\n"
                "⚠️ 未开展部门调查时，正文如实说明，不要编造个数。"
            ),
        },
    }


TEMPLATE_TABLE_SKELETONS: Dict[int, Dict[str, Any]] = {
    3: {
        "拟征收土地基本情况表": {
            "title": "表6-1 拟征收土地基本情况表",
            "columns": ["序号", "地块号", "土地坐落", "土地用途", "总面积(㎡)", "界址点数", "备注"],
            "fill_types": {
                "序号": "structural",
                "地块号": "data",
                "土地坐落": "data",
                "土地用途": "data",
                "总面积(㎡)": "data",
                "界址点数": "data",
                "备注": "data",
            },
            "rule": (
                "✏️ 本表所有数据必须从PDF提取的表格数据中获取（勘测定界报告）。\n"
                "如果PDF中有对应地块信息，直接使用；如果没有，标注【待补充】。\n"
                "数据来源：📄 0-勘测定界报告-.pdf → 第1页表格"
            ),
        },
    },
    # ═══ 第9章 各利益相关者对项目的反映（问卷表）═══
    #   🔴 统计维度**必须与问卷实际题目一致**：由 questionnaire_parser 的题目表派生，
    #      不在这里手写。旧版写死了「补偿标准满意度/社保安置满意度/主要关切问题」等
    #      问卷里根本没有的题目，模型只能整表填【待统计】（实测第9章出现 22 处占位）。
    5: {},
    9: _build_survey_skeletons(),
    # ═══ 第11章 分析预测风险源（风险等级表）═══
    11: {
        "风险因素初始风险等级表": {
            "title": "表11-1 风险因素初始风险等级表",
            "columns": ["序号", "风险类型", "风险因素描述", "风险等级"],
            "fill_types": {
                "序号": "structural",
                "风险类型": "fixed",       # 常见风险类型，模板提供
                "风险因素描述": "fixed",   # 标准描述，模板提供
                "风险等级": "data",        # 根据项目判定
            },
            "fixed_rows": [
                ["1", "征收程序方面的风险", "是否按照新的土地征收程序开展工作", "【根据项目判定】"],
                ["2", "土地征收补偿方案风险", "期望值过高，不满补偿方案", "【根据项目判定】"],
                ["3", "征收决策实施风险", "征收信息缺少透明度", "【根据项目判定】"],
                ["4", "征地社保名额确定引发的风险", "社保名额分配未明确的情况下，引发群众不满", "【根据项目判定】"],
                ["5", "征收决策资金风险", "责任单位管理不善导致资金不足；资金不足导致补偿款发放、社保资金缴纳存在问题；补偿款发放不能及时到位", "【根据项目判定】"],
                ["6", "对水系/环境影响方面的风险", "未提前了解地块周边水系情况，对河道防洪、灌溉、排涝功能产生影响", "【根据项目判定】"],
            ],
            "rule": "⚠️ 风险类型和风险因素描述照抄模板（fixed），风险等级根据Ch4四性分析判定（低/较低/一般/较高/高）",
        },
    },
    # ═══ 第12章 确定风险等级（评分表）═══
    12: {
        "直接定性指标表": {
            "title": "表12-1 直接定性指标",
            "columns": ["情形", "结论"],
            "fill_types": {"情形": "fixed", "结论": "fixed"},
            "fixed_rows": [
                ["超过50%以上群众反对的", "高风险"],
                ["可能引发200人以上群体性事件的", "高风险"],
                ["可能引发恶性案件", "高风险"],
                ["可能引发个人极端事件", "高风险"],
                ["20%以上～50%以下群众反对的", "中风险"],
                ["可能引发100人以上群体性事件的", "中风险"],
            ],
            "rule": "🔒 全部内容照抄模板，一字不改！这是DB32/T4013-2021标准的直接定性指标。",
        },
        "措施前风险等级量化评分表": {
            "title": "表12-2 稳评风险等级确定量化指标评价体系（措施前）",
            "columns": ["测评指标", "权重", "测评项目", "评分", "评分标准", "得分"],
            "fill_types": {
                "测评指标": "fixed",
                "权重": "fixed",
                "测评项目": "fixed",
                "评分": "fixed",
                "评分标准": "fixed",
                "得分": "data",  # 🔴 唯一需要根据项目填入的列！
            },
            "fixed_rows": [
                # 合法性 (10分)
                ["合法性", "10", "事项实施主体是否符合国家法律法规和规章的相关规定", "3", "全部符合的不计分，基本符合计2分，不符合的计3分", "【填入得分】"],
                ["合法性", "10", "内容是否符合国家的法律法规和规章；是否符合党和国家的路线方针政策，法定前置要件是否齐全", "5", "全部符合的不计分，基本符合计2分，不符合的计5分", "【填入得分】"],
                ["合法性", "10", "决策程序是否符合规定的议事决策规定", "2", "符合的不计分，不符合的计2分", "【填入得分】"],
                # 合理性 (25分)
                ["合理性", "25", "所涉及的利益相关方的界定是否明确", "2", "明确的不计分，基本明确的计1分，不明确的计2分", "【填入得分】"],
                ["合理性", "25", "对利益相关方的信息公开是否到位", "2", "到位的不计分，不到位的计2分", "【填入得分】"],
                ["合理性", "25", "群众满意度测评是否达标", "6", "满意度85%以上的不计分，70-85%以上计4分，低于70%的计6分", "【填入得分】"],
                ["合理性", "25", "会不会引发不同地区、行业、群体之间的攀比", "4", "引发攀比可能性较小的不计分，有可能引发攀比的计2分，引发攀比可能性较大的计4分", "【填入得分】"],
                ["合理性", "25", "专业论证是否可行", "3", "可行的不计分，不可行的计2分", "【填入得分】"],
                ["合理性", "25", "对所涉及群众的补偿、安置、保障等措施是否到位", "4", "到位的不计分，基本到位的计2分，不到位的计4分", "【填入得分】"],
                ["合理性", "25", "基层党委政府是否支持", "4", "支持的不计分，无所谓的计2分，不支持的计4分", "【填入得分】"],
                # 可行性 (10分)
                ["可行性", "10", "现有财政经济实力是否可以支撑相关成本支出", "3", "可以支撑的不计分，不能支撑的计3分", "【填入得分】"],
                ["可行性", "10", "对环境影响情况", "4", "不涉及环境影响的不计分，有影响的计2分，涉邻避决策的计4分", "【填入得分】"],
                ["可行性", "10", "现有技术条件是否具备", "3", "具备的不计分，基本具备的计1分，不具备的计3分", "【填入得分】"],
                # 可控性 (55分)
                ["可控性", "55", "群众意见分析", "35", "计分计算方法见表6-3", "【根据表6-3计算】"],
                ["可控性", "55", "负面舆论", "3", "无负面舆论的不计分，有负面舆论计1分，负面舆论较大的计3分", "【填入得分】"],
                ["可控性", "55", "恶意炒作", "3", "不会引发恶意炒作的不计分，可能引发恶意炒作计1分，较大可能引发恶意炒作的计3分", "【填入得分】"],
                ["可控性", "55", "维权人士插手", "3", "无维权人士插手的不计分，有维权人士插手计1分，维权人士插手较多的计3分", "【填入得分】"],
                ["可控性", "55", "敌对组织、敌对势力插手", "3", "无敌对组织、敌对势力插手的不计分，有敌对组织、敌对势力插手论计3分", "【填入得分】"],
                ["可控性", "55", "是否建立不稳定因素台账和报告制度", "3", "建立的不计分，已建立但不完善的计1分，未建立的计3分", "【填入得分】"],
                ["可控性", "55", "风险防范化解预案是否详实完整", "2", "详实完整的不计分，基本完整的计1分，不完整的计2分", "【填入得分】"],
                ["可控性", "55", "宣传解释和舆论引导工作是否到位", "3", "到位的不计分，基本到位的计1分，不到位的计3分", "【填入得分】"],
                # 合计
                ["合计", "合计", "合计", "合计", "合计", "【计算总分】"],
            ],
            "rule": (
                "🔒 测评指标、权重、测评项目、评分、评分标准——这5列照抄模板，一字不改！\n"
                "✏️ 只有「得分」列需要根据项目情况填入：\n"
                "  - 低风险项目（群众支持率>90%，程序合规）→ 大部分得0分\n"
                "  - 中风险项目 → 合理扣分，总分控制在30-60分\n"
                "  - 得分越低风险越低（0分=无风险）\n"
                "  - 如项目支持率96%，群众满意度高，则「群众满意度测评」得0分\n"
                "  - 「群众意见分析」得分根据表6-3计算：得分=反对率×风险概率系数×35"
            ),
        },
        "群众意见分析表": {
            "title": "表12-3 群众意见分析",
            "columns": ["序号", "反对率百分比a", "风险发生概率或激烈程度b", "得分"],
            "fill_types": {
                "序号": "structural",
                "反对率百分比a": "data",
                "风险发生概率或激烈程度b": "fixed",
                "得分": "data",
            },
            "fixed_rows": [
                ["1", "【填入实际反对率】", "肯定，有较大可能发生的或表示强烈反对的为1", "【a×b×35】"],
                ["2", "【填入实际反对率】", "有可能发生的或反对较强烈的为0.7", "【a×b×35】"],
                ["3", "【填入实际反对率】", "发生概率很小的或反对激烈程度一般的0.3", "【a×b×35】"],
            ],
            "rule": "🔒 风险发生概率描述照抄模板。反对率从用户数据获取，得分=a×b×35。",
        },
    # ═══ 第13章 防范和化解风险的工作建议 ═══,
        "措施后风险等级量化评分表": {
            "title": "表12-4 稳评风险等级确定量化指标评价体系（措施后）",
            "columns": ["测评指标", "权重", "测评项目", "评分", "评分标准", "得分"],
            "fill_types": {
                "测评指标": "fixed", "权重": "fixed", "测评项目": "fixed",
                "评分": "fixed", "评分标准": "fixed", "得分": "data",
            },
            "fixed_rows": [
                ["合法性", "10", "事项实施主体是否符合国家法律法规和规章的相关规定", "3", "全部符合的不计分，基本符合计2分，不符合的计3分", "【填入措施后得分】"],
                ["合法性", "10", "内容是否符合国家的法律法规和规章；是否符合党和国家的路线方针政策，法定前置要件是否齐全", "5", "全部符合的不计分，基本符合计2分，不符合的计5分", "【填入措施后得分】"],
                ["合法性", "10", "决策程序是否符合规定的议事决策规定", "2", "符合的不计分，不符合的计2分", "【填入措施后得分】"],
                ["合理性", "25", "所涉及的利益相关方的界定是否明确", "2", "明确的不计分，基本明确的计1分，不明确的计2分", "【填入措施后得分】"],
                ["合理性", "25", "对利益相关方的信息公开是否到位", "2", "到位的不计分，不到位的计2分", "【填入措施后得分】"],
                ["合理性", "25", "群众满意度测评是否达标", "6", "满意度85%以上的不计分，70-85%以上计4分，低于70%的计6分", "【填入措施后得分】"],
                ["合理性", "25", "会不会引发不同地区、行业、群体之间的攀比", "4", "引发攀比可能性较小的不计分，有可能引发攀比的计2分，引发攀比可能性较大的计4分", "【填入措施后得分】"],
                ["合理性", "25", "专业论证是否可行", "3", "可行的不计分，不可行的计2分", "【填入措施后得分】"],
                ["合理性", "25", "对所涉及群众的补偿、安置、保障等措施是否到位", "4", "到位的不计分，基本到位的计2分，不到位的计4分", "【填入措施后得分】"],
                ["合理性", "25", "基层党委政府是否支持", "4", "支持的不计分，无所谓的计2分，不支持的计4分", "【填入措施后得分】"],
                ["可行性", "10", "现有财政经济实力是否可以支撑相关成本支出", "3", "可以支撑的不计分，不能支撑的计3分", "【填入措施后得分】"],
                ["可行性", "10", "对环境影响情况", "4", "不涉及环境影响的不计分，有影响的计2分，涉邻避决策的计4分", "【填入措施后得分】"],
                ["可行性", "10", "现有技术条件是否具备", "3", "具备的不计分，基本具备的计1分，不具备的计3分", "【填入措施后得分】"],
                ["可控性", "55", "群众意见分析", "35", "计分计算方法见表6-3", "【填入措施后得分，应低于措施前】"],
                ["可控性", "55", "负面舆论", "3", "无负面舆论的不计分，有负面舆论计1分，负面舆论较大的计3分", "【填入措施后得分】"],
                ["可控性", "55", "恶意炒作", "3", "不会引发恶意炒作的不计分，可能引发恶意炒作计1分，较大可能引发恶意炒作的计3分", "【填入措施后得分】"],
                ["可控性", "55", "维权人士插手", "3", "无维权人士插手的不计分，有维权人士插手计1分，维权人士插手较多的计3分", "【填入措施后得分】"],
                ["可控性", "55", "敌对组织、敌对势力插手", "3", "无敌对组织、敌对势力插手的不计分，有敌对组织、敌对势力插手论计3分", "【填入措施后得分】"],
                ["可控性", "55", "是否建立不稳定因素台账和报告制度", "3", "建立的不计分，已建立但不完善的计1分，未建立的计3分", "【填入措施后得分】"],
                ["可控性", "55", "风险防范化解预案是否详实完整", "2", "详实完整的不计分，基本完整的计1分，不完整的计2分", "【填入措施后得分】"],
                ["可控性", "55", "宣传解释和舆论引导工作是否到位", "3", "到位的不计分，基本到位的计1分，不到位的计3分", "【填入措施后得分】"],
                ["合计", "合计", "合计", "合计", "合计", "【计算总分，应<措施前总分】"],
            ],
            "rule": (
                "🔒 测评指标、权重、测评项目、评分、评分标准——照抄模板！\n"
                "✏️ 得分应比措施前降低（措施有效降低了风险）\n"
                "⚠️ 措施后总分必须低于措施前总分！"
            ),
        },
        "措施前后得分对比表": {
            "title": "表12-5 措施前后得分对比表",
            "columns": ["序号", "评估维度", "措施前得分", "措施后得分", "变化幅度"],
            "fill_types": {"序号": "structural", "评估维度": "fixed", "措施前得分": "data", "措施后得分": "data", "变化幅度": "data"},
            "fixed_rows": [
                ["1", "合法性", "【来自Ch6】", "【本章计算】", "【计算差值】"],
                ["2", "合理性", "【来自Ch6】", "【本章计算】", "【计算差值】"],
                ["3", "可行性", "【来自Ch6】", "【本章计算】", "【计算差值】"],
                ["4", "可控性", "【来自Ch6】", "【本章计算】", "【计算差值】"],
                ["5", "合计", "【来自Ch6总分】", "【本章总分】", "【计算差值】"],
            ],
            "rule": "⚠️ 措施前得分必须与Ch6完全一致，措施后得分来自本章评分，变化幅度=措施后-措施前（应为负值）",
        },
    },
    13: {
        "风险防范与化解措施汇总表": {
            "title": "表13-1 风险防范与化解措施汇总表",
            "columns": ["序号", "风险类型", "防范化解措施", "责任主体", "完成时限"],
            "fill_types": {
                "序号": "structural",
                "风险类型": "fixed",       # 从Ch5引入
                "防范化解措施": "data",    # 根据Ch5风险逐条制定
                "责任主体": "data",        # 从项目数据获取
                "完成时限": "data",        # 根据项目计划
            },
            "fixed_rows": [
                ["1", "征收程序方面的风险", "【根据Ch5风险制定对应措施】", "【责任单位】", "【时限】"],
                ["2", "土地征收补偿方案风险", "【根据Ch5风险制定对应措施】", "【责任单位】", "【时限】"],
                ["3", "征收决策实施风险", "【根据Ch5风险制定对应措施】", "【责任单位】", "【时限】"],
                ["4", "征地社保名额确定引发的风险", "【根据Ch5风险制定对应措施】", "【责任单位】", "【时限】"],
                ["5", "征收决策资金风险", "【根据Ch5风险制定对应措施】", "【责任单位】", "【时限】"],
                ["6", "对水系/环境影响方面的风险", "【根据Ch5风险制定对应措施】", "【责任单位】", "【时限】"],
            ],
            "rule": (
                "🔒 风险类型照抄Ch5识别结果，必须一一对应。\n"
                "✏️ 每条风险至少制定一条具体措施（不是空话，要可操作）。\n"
                "✏️ 责任主体从filled_data提取（implement_unit/org_name）。\n"
                "✏️ 完成时限根据项目周期确定。"
            ),
        },
    },
    # ═══ 第12章 确定风险等级（措施后评分表）═══
}


# ═══════════════════════════════════════════════════════════════

class KnowledgeAgent(BaseAgent):
    """知识库模板/规范/格式检索专家Agent

    在章节生成前调用，为ChapterAgent准备完整的知识库上下文包：
    - structure_spec: 章节结构规范（标题层级、小节列表）
    - format_template: 表格/格式模板（表头、列名、格式要求）
    - regulation_context: 相关法规条文
    - example_fragments: 历史报告范文片段
    - writing_constraints: 写作约束（字数范围、固定内容）
    - table_skeletons: 模板表格骨架（固定内容+数据槽位）
    """

    name = "KnowledgeAgent"
    description = "从知识库检索模板结构、格式规范、法规依据和历史范文，为章节生成提供知识库上下文"
    covered_steps = []

    async def think(self, state: dict) -> Dict[str, Any]:
        """分析当前需要为哪些章节准备知识库上下文"""
        current_chapter = state.get("current_chapter", 1)
        generation_mode = state.get("generation_mode", "")

        if generation_mode == "chapter_by_chapter":
            chapters_to_retrieve = [current_chapter]
        else:
            chapters_to_retrieve = list(range(1, 11))

        kb_cache = state.get("_knowledge_cache", {})
        uncached = [ch for ch in chapters_to_retrieve if ch not in kb_cache]

        steps = [
            f"📚 需要检索 {len(chapters_to_retrieve)} 个章节的知识库上下文",
            f"📦 已缓存 {len(kb_cache)} 个章节",
        ]
        if uncached:
            steps.append(f"🔍 待检索章节: {uncached}")

        return {
            "summary": f"KnowledgeAgent: 为第{current_chapter}章准备知识库上下文",
            "steps": steps,
            "chapters_to_retrieve": uncached,
            "all_chapters": chapters_to_retrieve,
        }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """执行知识库检索，为每个章节构建完整的知识库上下文包"""
        chapters_to_retrieve = plan.get("chapters_to_retrieve", [])
        kb_results = {}

        for ch_num in chapters_to_retrieve:
            context_pack = await self._retrieve_chapter_context(state, ch_num)
            kb_results[ch_num] = context_pack

        return {"kb_results": kb_results, "status": "completed"}

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        """验证知识库检索结果的完整性"""
        issues = []
        for ch_num, pack in result.get("kb_results", {}).items():
            if not pack.get("structure_spec"):
                issues.append(f"第{ch_num}章: 缺少结构规范")
            if not pack.get("regulation_context") and not pack.get("format_template"):
                issues.append(f"第{ch_num}章: 法规上下文和格式模板均为空")
        return issues

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """将知识库上下文缓存到state中供ChapterAgent使用"""
        kb_cache = state.setdefault("_knowledge_cache", {})
        for ch_num, pack in result.get("kb_results", {}).items():
            kb_cache[ch_num] = pack

        kb_summary = state.setdefault("_kb_context_summary", {})
        for ch_num, pack in result.get("kb_results", {}).items():
            kb_summary[ch_num] = {
                "has_structure": bool(pack.get("structure_spec")),
                "has_format": bool(pack.get("format_template")),
                "has_regulation": bool(pack.get("regulation_context")),
                "has_example": bool(pack.get("example_fragments")),
                "structure_sections": pack.get("structure_spec", {}).get("sections", []),
                "required_tables": pack.get("structure_spec", {}).get("required_tables", []),
            }

        state["_knowledge_cache"] = kb_cache
        state["_kb_context_summary"] = kb_summary
        return state

    # ═══════════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════════

    async def _retrieve_chapter_context(
        self, state: dict, chapter_number: int
    ) -> Dict[str, Any]:
        """为单个章节检索完整的知识库上下文包"""
        structure_spec = CHAPTER_STRUCTURE_SPECS.get(chapter_number, {})
        rag_context = await self._rag_retrieve(state, chapter_number)
        template_context = self._extract_from_templates(state, chapter_number)

        return {
            "structure_spec": structure_spec,
            "format_template": rag_context.get("format_context", ""),
            "regulation_context": rag_context.get("regulation_context", ""),
            "example_fragments": rag_context.get("example_context", ""),
            "template_excerpts": template_context,
            "writing_constraints": {
                "min_words": structure_spec.get("min_words", 300),
                "max_words": structure_spec.get("max_words", 2000),
                "required_tables": structure_spec.get("required_tables", []),
                "fixed_content": structure_spec.get("fixed_content", []),
                "sections": structure_spec.get("sections", []),
            },
        }

    async def _rag_retrieve(self, state: dict, chapter_number: int) -> Dict[str, str]:
        """通过共享RAG服务检索法规/格式/范文上下文"""
        try:
            from app.rag.rag_service import rag_service

            session_id = state.get("session_id", "")
            project_context = state.get("project_context", "")

            result = await rag_service.retrieve_for_chapter(
                chapter_number=chapter_number,
                session_id=session_id,
                project_context=project_context,
                n_results=25,
                domain=state.get("_domain") or state.get("_conversation_domain"),
            )

            return {
                "format_context": result.get("chapter_context", ""),
                "regulation_context": result.get("local_regulation_context", ""),
                "example_context": result.get("example_context", ""),
            }
        except Exception as e:
            logger.warning(f"KnowledgeAgent RAG ch{chapter_number} failed: {e}")
            return {"format_context": "", "regulation_context": "", "example_context": ""}

    def _extract_from_templates(self, state: dict, chapter_number: int) -> str:
        """从state中的模板信息提取对应章节的内容片段"""
        pdf_texts = state.get("_pdf_texts", {})
        if not pdf_texts:
            return ""

        chapter_keywords = CHAPTER_KB_QUERIES.get(chapter_number, {}).get("structure", "")
        if not chapter_keywords:
            return ""

        relevant_parts = []
        keywords = chapter_keywords.split()[:5]
        for path, text in pdf_texts.items():
            for kw in keywords:
                if kw in text:
                    for para in text.split("\n\n"):
                        if kw in para and len(para) > 20:
                            relevant_parts.append(para[:500])
                            break
                    break

        return "\n\n".join(relevant_parts[:3]) if relevant_parts else ""


def get_knowledge_context_for_chapter(state: dict, chapter_number: int) -> Dict[str, Any]:
    """便捷函数：从state缓存中获取指定章节的知识库上下文

    供 ChapterAgentBase 调用，无需重新创建 KnowledgeAgent 实例。
    """
    kb_cache = state.get("_knowledge_cache", {})
    return kb_cache.get(chapter_number, {
        "structure_spec": CHAPTER_STRUCTURE_SPECS.get(chapter_number, {}),
        "format_template": "",
        "regulation_context": "",
        "example_fragments": "",
        "template_excerpts": "",
        "writing_constraints": {
            "min_words": 300, "max_words": 2000,
            "required_tables": [], "fixed_content": [], "sections": [],
        },
    })

# ═══════════════════════════════════════════════════════════════
# 🔴 用 chapter_definitions（唯一事实源）补齐并校正上面几张表：
#    - 章节结构规范的小节，一律以 CHAPTER_SECTION_SPECS 为准，不再各存一份；
#    - 知识库检索模板：缺章的用 CHAPTER_RAG_QUERIES 兜底；
#    - 章标题一律取自 chapter_definitions。
# ═══════════════════════════════════════════════════════════════

def _sync_with_chapter_definitions() -> None:
    from app.agent.chapter_definitions import (
        ALL_CHAPTERS, get_chapter_title, get_chapter_section_specs, get_chapter_rag_query)

    for n in ALL_CHAPTERS:
        title = get_chapter_title(n)

        # ① 结构规范：小节以唯一事实源为准
        spec = CHAPTER_STRUCTURE_SPECS.setdefault(n, {})
        # 🔴 小节以唯一事实源为准：没有固定小节的章（评审表 / 小节动态生成的
        #    第11、13章）要**清空**旧表里残留的小节，否则会带着过期结构走。
        secs = [x.get("title", "") for x in get_chapter_section_specs(n)]
        spec["sections"] = secs
        spec.setdefault("title", title)
        spec.setdefault("required_tables", [])
        spec.setdefault("fixed_content", [])
        spec.setdefault("min_words", 300)
        spec.setdefault("max_words", 3000)

        # ② 知识库检索模板：缺章的用 RAG 检索词兜底
        kb = CHAPTER_KB_QUERIES.setdefault(n, {})
        if not kb:
            q = get_chapter_rag_query(n)
            kb["structure"] = f"第{n}章 {title} {q}"
            kb["format"] = f"{title} 格式 写法 规范"
            kb["regulation"] = "社会稳定风险评估 法规 标准"


_sync_with_chapter_definitions()
