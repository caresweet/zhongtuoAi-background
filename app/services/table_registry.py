"""Table Registry — 报告表格的格式参考（仅列结构，不包含固定内容）。

表格不再由系统硬编码渲染。章节 agent 根据实际数据动态设计表格，
本模块只提供「列结构 + 表格用途」作为格式参考。

数据来源：历史模板报告（洞庭湖路稳评）的表格列结构。
"""

from typing import Dict, Any

# 表格格式参考：name -> {chapter, columns, description}
# 只用于 build_chapter_prompt 生成格式参考文本，不再渲染固定内容
TABLE_REGISTRY: Dict[str, Dict[str, Any]] = {
    # 🔴 列结构**必须与勘测定界报告一致**（业主：「用勘测定界的表格格式」）。
    #    原来多一个源表没有的「备注」列、章号还写着第1章（实际渲染在第6章）。
    #    真正的内容由 `land_survey_parser.to_extracted_tables()` 就地渲染，
    #    这里只是给模型看的**格式参考**，不能自成一套。
    "ch1_land_info": {
        "chapter": 6,
        "columns": ["序号", "地块号", "土地坐落", "土地用途", "总面积(㎡)", "界址点数"],
        "description": "拟征收土地基本情况表（勘测定界地块情况汇总表，表6-1）",
    },
    "ch2_regulation_list": {
        "chapter": 2,
        "columns": ["序号", "类别", "法规/标准名称", "文号/编号"],
        "description": "评估依据法规清单",
    },
    # 🔴 归属校正（2026-09-30）：问卷统计表在**第9章**（表9-1 个人 / 表9-2 单位），
    #    原来的 `ch3_public_survey` / `ch3_dept_survey` 归属已过时 ——
    #    现行 14 章制下第3章是「评审表」章（`CHAPTER_KIND[3] == "table"`，数据驱动渲染）。
    #    这两张表的**题面必须与问卷原件逐字一致**（业主评审硬要求），实测 LLM 手写会走样
    #    （「您是」→「您的身份」、部门原件题面被概括成「对决策的了解程度」），
    #    人数也有被改写/对调的风险。因此**一律由系统渲染**：
    #    正文只在对应位置输出 `[TABLE:ch9_public_survey]` / `[TABLE:ch9_dept_survey]` 标记，
    #    表结构与数据由 `table_generation_service` + `questionnaire_parser` 直出。
    "ch9_public_survey": {
        "chapter": 9,
        "columns": ["调查内容", "选项", "人数（人）", "比例（%）"],
        "description": "表9-1 个人问卷统计汇总表（🔴 系统渲染：正文只放 [TABLE:ch9_public_survey] 标记，不要手写该表）",
    },
    "ch9_dept_survey": {
        "chapter": 9,
        "columns": ["调查内容", "选项", "个数（个）", "比例（%）"],
        "description": "表9-2 单位（部门）问卷统计汇总表（🔴 系统渲染：正文只放 [TABLE:ch9_dept_survey] 标记，不要手写该表）",
    },
    # 🔴 第12章的两张量化表：注册表里一直没有对应项，只编出编号没有名字。
    #    名称按**参照件模板**逐字对：表12-2 是「（措施前）」那一套，
    #    第4章的表4-2 才是「（措施后）」——两章各一套，别混。
    "ch12_quant_system": {
        "chapter": 12,
        "columns": ["测评指标", "权重", "测评项目", "评分", "评分标准", "得分"],
        "description": "稳评风险等级确定量化指标评价体系（措施前）",
    },
    # 🔴 参照件里这张表叫「群众意见分析」（第4章表4-3、第12章表12-3 各一张，
    #    同源同结构）。我方原来写成「风险发生概率及激烈程度评分表」，名不对。
    "ch12_probability": {
        "chapter": 12,
        "columns": ["序号", "反对率百分比a", "风险发生概率或激烈程度b", "得分"],
        "description": "群众意见分析",
    },
    # 🔴 第4章「社会稳定风险评估工作报告」按模板要出**措施后**那一套两张表。
    "ch4_quant_system": {
        "chapter": 4,
        "columns": ["测评指标", "权重", "测评项目", "评分", "评分标准", "得分"],
        "description": "稳评风险等级确定量化指标评价体系（措施后）",
    },
    "ch4_opinion": {
        "chapter": 4,
        "columns": ["序号", "反对率百分比a", "风险发生概率或激烈程度b", "得分"],
        "description": "群众意见分析",
    },
    "ch5_risk_factors": {
        "chapter": 5,
        "columns": ["序号", "风险类型", "风险因素描述", "风险等级"],
        "description": "风险因素初始风险等级表",
    },
    "ch6_direct_indicators": {
        "chapter": 6,
        "columns": ["情形", "结论"],
        "description": "直接定性指标",
    },
    "ch6_scoring": {
        "chapter": 6,
        "columns": ["测评指标", "权重", "测评项目", "评分", "评分标准", "得分"],
        "description": "措施前风险等级量化评分表",
    },
    "ch6_opposition": {
        "chapter": 6,
        "columns": ["序号", "反对率百分比", "风险发生概率或激烈程度", "得分"],
        "description": "群众意见分析",
    },
    "ch7_measures": {
        "chapter": 7,
        "columns": ["序号", "风险类型", "防范化解措施", "责任主体", "完成时限"],
        "description": "风险防范与化解措施汇总表",
    },
    "ch8_scoring_after": {
        "chapter": 8,
        "columns": ["测评指标", "权重", "测评项目", "评分", "评分标准", "得分"],
        "description": "措施后风险等级量化评分表",
    },
    "ch8_comparison": {
        "chapter": 8,
        "columns": ["序号", "评估维度", "措施前得分", "措施后得分", "变化幅度", "说明"],
        "description": "措施前后得分对比表",
    },
}
