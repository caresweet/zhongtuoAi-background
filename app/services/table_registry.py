"""Table Registry — 报告表格的格式参考（仅列结构，不包含固定内容）。

表格不再由系统硬编码渲染。章节 agent 根据实际数据动态设计表格，
本模块只提供「列结构 + 表格用途」作为格式参考。

数据来源：历史模板报告（洞庭湖路稳评）的表格列结构。
"""

from typing import Dict, Any

# 表格格式参考：name -> {chapter, columns, description}
# 只用于 build_chapter_prompt 生成格式参考文本，不再渲染固定内容
TABLE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "ch1_land_info": {
        "chapter": 1,
        "columns": ["序号", "地块号", "土地坐落", "土地用途", "总面积(㎡)", "界址点数", "备注"],
        "description": "拟征收土地基本情况表",
    },
    "ch2_regulation_list": {
        "chapter": 2,
        "columns": ["序号", "类别", "法规/标准名称", "文号/编号"],
        "description": "评估依据法规清单",
    },
    "ch3_public_survey": {
        "chapter": 3,
        "columns": ["调查内容", "选项", "人次", "比例（%）"],
        "description": "公众意见调查分析表",
    },
    "ch3_dept_survey": {
        "chapter": 3,
        "columns": ["调查内容", "选项", "人次", "比例（%）"],
        "description": "部门意见调查分析表",
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
