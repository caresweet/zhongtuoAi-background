# -*- coding: utf-8 -*-
"""知识库重建配置：章节归一表（单一真相源）。

把多篇稳评范文的差异命名章节，按其「内容语义」归并到统一的类型库。
每类一个 Chroma collection。章节目录类型 = 该 collection 名。

手工梳理（2026-09-08），覆盖 8 篇完整稳评范文。编号固定，改动需同步下方映射。
"""

# 类型库总表：类型名(collection 名) + 中文说明 + 生成侧章节用标的
TYPES = {
    "report_basic":    "第一章 稳评评审表",            # 评审表内容
    "report_desc":     "编制说明",                      # 理由/目的/原则/公司简介
    "report_base":     "评估依据",                      # 法律法规/决策文件/规范
    "decision_info":   "决策事项基本情况",             # ★ 决策名称/主体/地理位置/范围/利益相关者
    "risk_plan":       "稳评工作方案（方法与过程）",    # 指导思想/原则/范围/方法/步骤/时间/人员
    "stakeholder_ref": "各利益相关者对决策事项的反映",  # 风险调查/各方意见/诉求
    "four_dimension":  "决策事项合法性、合理性、可行性、可控性分析",  # 四性分析
    "risk_source":     "分析预测风险源",                # 风险源识别
    "risk_measure":    "提出风险防范与化解措施的工作建议",  # 防范化解措施
    "risk_concl":      "稳评工作报告（确定风险等级）",  # 结论/风险等级
    "emergency":       "突发性事件或不稳定因素处置应对工作预案",  # 应急预案
    "appendix":        "附件",                          # 附录
}

# 每篇范文 → 各类型库对应的「章节标题关键字」
# 值=该类型库在此文中对应章节应包含的关键字；None=此文无此类。
# 抽取出章节后按关键字把正文切片分发到对应 collection。
DOC_TYPE_MAP = {
    # 清拟征告〔2024〕PXX 号案卷（12章，统一结构）
    "清拟征告19": {
        "report_basic": "评审表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者对决策事项的反映",
        "four_dimension": "合法性", "risk_source": "分析预测风险源",
        "risk_measure": "防范与化解", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
    "清拟征告18": {  # 同 19
        "report_basic": "评审表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者", "four_dimension": "合法性",
        "risk_source": "风险源", "risk_measure": "防范与化解", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
    "清拟征告13": {  # 同
        "report_basic": "评审表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者", "four_dimension": "合法性",
        "risk_source": "风险源", "risk_measure": "防范与化解", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
    "清拟征告12": {  # 同上（部分章略）
        "report_basic": "评审表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者", "four_dimension": "合法性",
        "risk_source": "风险源", "risk_measure": "防范与化解", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
    "清拟征告11": {  # 同
        "report_basic": "评审表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者", "four_dimension": "合法性",
        "risk_source": "风险源", "risk_measure": "防范与化解", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
    # 稳评报告7（10章决策式）
    "稳评报告7": {
        "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "拟征收决策基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者对拟决策事项的反映",
        "four_dimension": "综合性分析", "risk_source": "分析预测风险源",
        "risk_measure": "防范与化解", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": None,
    },
    # 洞庭湖（12章）
    "洞庭湖": {
        "report_basic": "稳评评审报告表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者对决策事项的反映", "four_dimension": "合法性",
        "risk_source": "分析预测风险源", "risk_measure": "防范和化解", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
    # 金湖范文（嵌入式，无章，有 1.X/2.X 小节）
    "金湖范文": {
        "report_desc": None, "report_base": "评估依据",
        "decision_info": "决策名称", "risk_plan": None,
        "stakeholder_ref": "问卷调查", "four_dimension": "合法性分析",
        "risk_source": "风险因素", "risk_measure": "防范化解", "risk_concl": "评估结论",
        "emergency": "应急预案",
    },
    # 某产业园区（8章，评估式）
    "某产业园区": {
        "report_basic": "项目概况", "report_desc": None, "report_base": "评估依据与原则",
        "decision_info": "项目概况", "risk_plan": "评估主体与方法",
        "stakeholder_ref": "风险调查", "four_dimension": None,
        "risk_source": "风险识别与分析", "risk_measure": "风险防范与化解措施",
        "risk_concl": "评估结论|风险等级判定", "emergency": None,
    },
    # ── 新增入库范文（2026-09-09 批量导入）── 语料本身是 12 章案卷式
    #    （注：**输出的报告结构是十四章**，参照件《白马湖…报告》；
    #      语料只作内容/行文风格参考，章节名一律以 chapter_definitions 为准）
    "盱眙5号": {
        "report_basic": "评审表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者", "four_dimension": "合法性",
        "risk_source": "风险源", "risk_measure": "防范", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
    "黄码17": {  # 同案卷式（章序略有差异，关键字匹配即可）
        "report_basic": "评审表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者", "four_dimension": "合法性",
        "risk_source": "风险源", "risk_measure": "防范", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
    "大湖娃": {  # 同
        "report_basic": "评审表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者", "four_dimension": "合法性",
        "risk_source": "风险源", "risk_measure": "防范", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
    "朱坝西朱坝": {  # 同
        "report_basic": "评审表", "report_desc": "编制说明", "report_base": "编制依据",
        "decision_info": "决策事项基本情况", "risk_plan": "稳评工作方案",
        "stakeholder_ref": "各利益相关者", "four_dimension": "合法性",
        "risk_source": "风险源", "risk_measure": "防范", "risk_concl": "稳评工作报告",
        "emergency": "突发性事件", "appendix": "附件",
    },
}

if __name__ == "__main__":
    print("归一表配置已加载。类型库:")
    for k, v in TYPES.items():
        print(f"  [{k}] {v}")
    print(f"覆盖 {len(DOC_TYPE_MAP)} 篇范文")