#!/usr/bin/env python3
"""
多Agent统一协调器 — 协调多个AI Agent协同生成稳评报告

基于新的多Agent协同架构：
  Coordinator
    → KnowledgeAgent（知识库检索）
    → DataValidatorAgent（数据校验）
    → 3个 Writer Agent 并行生成
    → FormatComplianceAgent（格式审核）
    → CrossReferenceAgent（一致性校验）
    → 汇总输出

用法:
  python multi_agent.py <数据JSON路径> <输出JSON路径>

  数据JSON由 template_parser.py 生成，用户填写后传入。

依赖:
  - DASHSCOPE_API_KEY 环境变量
  - 需要 app.agent.agents 中的协同Agent模块
"""
import os
import sys
import json
import time
import asyncio
import logging
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


# ============================================================
# LLM 配置 (DashScope qwen-plus)
# ============================================================
LLM_API_BASE = os.environ.get("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_API_KEY = os.environ.get("DASHSCOPE_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen-plus")
LLM_MAX_TOKENS = 4000
LLM_TIMEOUT = 120


def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = LLM_MAX_TOKENS) -> str:
    """调用LLM（纯stdlib HTTP，兼容沙盒）"""
    url = f"{LLM_API_BASE}/chat/completions"
    data = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {LLM_API_KEY}")

    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"[LLM Error] HTTP {e.code}: {body[:200]}")
        return f"[生成失败: HTTP {e.code}]"
    except Exception as e:
        print(f"[LLM Error] {e}")
        return f"[生成失败: {str(e)[:100]}]"


# ============================================================
# 知识库规范检索（来自KnowledgeAgent的静态规范）
# ============================================================

try:
    from app.agent.agents.knowledge_agent import CHAPTER_STRUCTURE_SPECS, CHAPTER_KB_QUERIES
except ImportError:
    # 如果无法导入，使用内置规范
    CHAPTER_STRUCTURE_SPECS = {}
    CHAPTER_KB_QUERIES = {}


def get_chapter_constraints(chapter_number: int) -> Dict:
    """获取指定章节的结构约束"""
    return CHAPTER_STRUCTURE_SPECS.get(chapter_number, {
        "sections": [],
        "required_tables": [],
        "min_words": 300,
        "max_words": 2000
    })


# ============================================================
# 章节Agent系统提示词 — 增强知识库约束版
# ============================================================

def build_system_prompt(agent_type: str) -> str:
    """构建增强版系统提示词，包含知识库结构约束"""

    base_identity = "你是{company_name}的社会稳定风险评估报告撰写专家。"

    if agent_type == "ch1_2":
        spec1 = get_chapter_constraints(1)
        spec2 = get_chapter_constraints(2)
        sections_1 = "\n".join(f"  - {s}" for s in spec1.get("sections", [
            "1.1 决策名称", "1.2 稳评责任单位", "1.3 拟征地位置",
            "1.4 征收范围面积", "1.5 资金筹措", "1.6 实施周期"
        ]))
        sections_2 = "\n".join(f"  - {s}" for s in spec2.get("sections", [
            "2.1 评估过程", "2.2 评估依据"
        ]))
        return f"""{base_identity}
你负责撰写报告的**第1章（拟征收决策基本概况）和第2章（评估过程、方法和依据）**。

## 知识库模板结构要求（必须严格遵守）
### 第1章应包含：
{sections_1}

### 第2章应包含：
{sections_2}

## 写作要求
- 严格使用用户提供的数据，不自行编造任何数值
- 法规依据部分从知识库提取19项法律法规
- 格式：机关公文书面语体，严谨、客观
- 字数：第1章{spec1.get('min_words', 300)}-{spec1.get('max_words', 1500)}字，第2章{spec2.get('min_words', 300)}-{spec2.get('max_words', 2000)}字
- 每小节之后输出"本节核心：XXX"一句话总结
- 只输出正文，不输出过程性解释
- 严格按照上述结构组织，不增删小节"""

    elif agent_type == "ch3_5":
        spec3 = get_chapter_constraints(3)
        spec4 = get_chapter_constraints(4)
        spec5 = get_chapter_constraints(5)
        return f"""{base_identity}
你负责撰写报告的**第3章（风险因素调查）、第4章（综合分析）和第5章（风险因素识别）**。

## 知识库模板结构要求
### 第3章核心小节：
{chr(10).join('  - ' + s for s in spec3.get('sections', ['3.1-3.6 风险调查']))}

### 第4章核心小节（四维分析）：
{chr(10).join('  - ' + s for s in spec4.get('sections', ['4.1 合法性', '4.2 合理性', '4.3 可行性', '4.4 可控性']))}

### 第5章核心小节：
{chr(10).join('  - ' + s for s in spec5.get('sections', ['5.1 识别方法', '5.2 风险因素详述']))}

## 写作要求
- 基于用户提供的支持率、知晓率等数据进行合理推断分析
- 合理性/可行性/可控性分析每小节3-5句话
- 风险因素详述每个1-2段，引用相关政策文件
- 专业性：使用稳评行业术语，引用DB32/T4013-2021规范
- 每小节之后输出"本节核心：XXX"一句话总结
- 只输出正文，不输出过程性解释"""

    else:  # ch6_10
        return f"""{base_identity}
你负责撰写报告的**第6章（措施前风险等级研判）、第7章（风险防范与化解措施）、第8章（措施后风险等级评估）、第9章（评估结论）和第10章（应急预案）**。

## 知识库模板结构要求
- 第6章：按DB32/T4013-2021量化指标体系打分，附评分表
- 第7章：5项防范措施（宣传+补偿+资金+社保+信访），附措施汇总表
- 第8章：措施后重新评分（得分应低于措施前），附对比表
- 第9章：四性总结+等级判定+4-5条实施建议
- 第10章：12小节完整应急预案（目的/依据/范围/原则/组织/职责/预警/处置/保障/奖惩）

## 写作要求
- 措施前得分通常13-25分（低风险），措施后低于措施前3-5分
- 应急预案组织架构中使用{{responsibility_unit}}的机构名称
- 每小节之后输出"本节核心：XXX"一句话总结
- 只输出正文，不输出过程性解释"""


def build_agent_prompt(agent_type: str, data: Dict, context: Dict = None) -> str:
    """根据Agent类型构建用户提示词（与原逻辑一致）"""
    bi = data.get("basic_info", {})
    sd = data.get("survey_data", {})
    merged = {**bi, **sd}

    if agent_type == "ch1_2":
        return f"""请基于以下项目数据，撰写社会稳定风险评估报告的**第1章和第2章**。

## 项目数据
- 决策名称：{merged.get('decision_name', '待提供')}
- 公告文号：{merged.get('bulletin_number', '待提供')}
- 项目名称：{merged.get('project_name', '待提供')}
- 稳评责任单位：{merged.get('responsibility_unit', '待提供')}
- 委托日期：{merged.get('commission_month', '待提供')}
- 拟征地位置：{merged.get('location_community', '待提供')}（位于{merged.get('location_street', '')}）
- 征收面积：{merged.get('area_hectares', '')}公顷（{merged.get('area_mu', '')}亩）
- 土地性质：{merged.get('land_type', '')}
- 征地用途：{merged.get('land_use', '')}
- 资金：{merged.get('fund_per_mu', '')}万元/亩，约{merged.get('total_fund', '')}万元
- 资金来源：{merged.get('fund_source', '')}
- 公告日期：{merged.get('bulletin_date', '')}

## 要求
按照知识库标准模板结构逐一撰写，每小节以"本节核心：XXX"结束。"""

    elif agent_type == "ch3_5":
        return f"""请基于以下调研数据，撰写报告的**第3章、第4章和第5章**。

## 调研数据
- 调查时间：{merged.get('survey_start', '')} 至 {merged.get('survey_end', '')}
- 发放问卷：{merged.get('questionnaires_count', '')}份
- 群众支持率：{merged.get('support_rate', '')}%
- 知晓率：{merged.get('awareness_rate', '')}%
- 基层组织意见：{merged.get('grassroots_opinion', '')}
- 村民主要诉求：{merged.get('villager_demands', '')}
- 网络舆情：{merged.get('online_opinion', '')}
- 决策名称：{merged.get('decision_name', '')}
- 征收面积：{merged.get('area_hectares', '')}公顷
- 资金来源：{merged.get('fund_source', '')}

## 要求
合法/合理/可行/可控四维分析要专业具体，风险详述引用政策文件。每小节以"本节核心：XXX"结束。"""

    elif agent_type == "ch6_10":
        return f"""请基于以下数据，撰写报告的**第6-10章**。

## 项目数据
- 决策名称：{merged.get('decision_name', '')}
- 稳评责任单位：{merged.get('responsibility_unit', '')}
- 征收面积：{merged.get('area_hectares', '')}公顷
- 支持率：{merged.get('support_rate', '')}%
- 知晓率：{merged.get('awareness_rate', '')}%

## 要求
措施前得分13-25分（低风险），措施后低于措施前3-5分。应急预案使用{merged.get('responsibility_unit', '责任单位')}机构名称。每小节以"本节核心：XXX"结束。"""

    return ""


def generate_ai_content(agent_type: str, data: Dict) -> str:
    """调用Agent生成指定章节内容"""
    system = build_system_prompt(agent_type)
    user_prompt = build_agent_prompt(agent_type, data)

    print(f"\n{'='*60}")
    print(f"[Agent: {agent_type}] 开始生成（知识库约束模式）...")
    print(f"{'='*60}")

    start = time.time()
    content = call_llm(system, user_prompt, max_tokens=4000)
    elapsed = time.time() - start

    print(f"[Agent: {agent_type}] 生成完成 ({elapsed:.1f}s), {len(content)} 字符")
    return content


# ============================================================
# 段落映射（与report_filler.py一致）
# ============================================================

CHAPTER_TO_PARAGRAPH_MAP = {
    "ch1_2": [182, 184, 187, 189, 191, 200, 263, 264],
    "ch3_5": [293, 295, 297, 319, 321, 323, 326, 328, 330, 333, 335, 337, 339, 353, 355, 357, 359],
    "ch6_10": [393, 395, 397, 399, 401, 420, 422, 424, 427, 442]
    }


def split_ai_content_to_paragraphs(content: str, agent_type: str) -> Dict[int, str]:
    """将AI生成的整章内容按 ## 标题分割映射到各段落索引"""
    import re
    result = {}
    indices = CHAPTER_TO_PARAGRAPH_MAP.get(agent_type, [])
    sections = re.split(r'\n(?=#{2,3}\s)', content)
    sections = [s.strip() for s in sections if s.strip()]

    for i, section in enumerate(sections):
        if i < len(indices):
            lines = section.split("\n")
            content_lines = [l for l in lines if not l.startswith("#")]
            body = "\n".join(content_lines).strip()
            if body:
                result[indices[i]] = body

    if len(sections) > len(indices):
        remaining = []
        for s in sections[len(indices):]:
            lines = s.split("\n")
            remaining.extend([l for l in lines if not l.startswith("#")])
        if remaining and indices:
            result[indices[-1]] = result.get(indices[-1], "") + "\n" + "\n".join(remaining)

    return result


# ============================================================
# 格式合规检查（简化版FormatComplianceAgent）
# ============================================================

def quick_compliance_check(agent_type: str, content: str) -> Dict:
    """快速格式合规检查"""
    import re
    issues = []
    score = 100

    # 禁止内容
    prohibited = [r'```json', r'好的[，,]', r'当然可以', r'哈哈|呵呵']
    for p in prohibited:
        if re.search(p, content):
            issues.append(f"发现禁止内容: {p}")
            score -= 10

    # 待补充标记
    placeholders = content.count("【待补充】") + content.count("【待用户补充】")
    if placeholders > 10:
        issues.append(f"待补充标记过多({placeholders}个)")
        score -= 10

    # 字数检查
    if len(content) < 300:
        issues.append(f"内容过短({len(content)}字)")
        score -= 15

    return {"score": max(0, score), "issues": issues, "compliant": score >= 60}


# ============================================================
# Coordinator：多Agent协调主流程
# ============================================================

def run_coordinator(data_path: str, output_path: str) -> Dict:
    """
    多Agent协调主流程（增强版）：
    1. 加载数据
    2. DataValidator：校验数据完整性
    3. KnowledgeAgent：准备知识库上下文（内置规范）
    4. 并行启动3个Writer Agent（带知识库约束）
    5. FormatCompliance：格式合规审核
    6. CrossReference：跨章节一致性校验
    7. 汇总输出
    """
    print("=" * 70)
    print("  众拓稳评报告多Agent协同生成系统 v3.0")
    print("=" * 70)

    # 1. 加载数据
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"\n[Coordinator] 数据加载完成")
    print(f"  基础信息字段: {len(data.get('basic_info', {}))} 项")
    print(f"  调研数据字段: {len(data.get('survey_data', {}))} 项")

    # 2. DataValidator：数据完整性校验
    bi = data.get("basic_info", {})
    sd = data.get("survey_data", {})
    critical_missing = []
    if not bi.get("decision_name"):
        critical_missing.append("decision_name（决策名称）")
    if not bi.get("responsibility_unit"):
        critical_missing.append("responsibility_unit（责任单位）")
    if not bi.get("location_community"):
        critical_missing.append("location_community（拟征地位置）")

    if critical_missing:
        print(f"\n[DataValidator] ⚠️ 缺失关键数据: {critical_missing}")
        print("  → 将使用【待补充】标记，建议补充后重新生成")
    else:
        print(f"\n[DataValidator] ✅ 关键数据完整")

    data_quality = max(0, 100 - len(critical_missing) * 20)
    print(f"  数据完整度: {data_quality}分")

    # 3. KnowledgeAgent：知识库规范（内置）
    print(f"\n[KnowledgeAgent] 📚 加载知识库结构规范...")
    for ch_num in range(1, 11):
        spec = get_chapter_constraints(ch_num)
        if spec.get("sections"):
            print(f"  第{ch_num}章: {len(spec['sections'])}个小节, "
                  f"字数{spec.get('min_words', 0)}-{spec.get('max_words', 0)}")

    # 4. 并行启动3个Writer Agent
    agents = ["ch1_2", "ch3_5", "ch6_10"]
    agent_names = {
        "ch1_2": "Ch1-2 Writer（基本概况+评估过程）",
        "ch3_5": "Ch3-5 Writer（风险调查+综合+识别）",
        "ch6_10": "Ch6-10 Writer（研判+防范+结论+预案）"
    }

    print(f"\n[Coordinator] 启动 {len(agents)} 个Writer Agent并行生成...")
    print("  " + "\n  ".join(agent_names.values()))

    agent_results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(generate_ai_content, agent, data): agent
            for agent in agents
        }
        for future in as_completed(futures):
            agent = futures[future]
            try:
                content = future.result()
                agent_results[agent] = content
                print(f"\n[Coordinator] {agent_names[agent]} 完成 ✓ ({len(content)}字符)")
            except Exception as e:
                print(f"\n[Coordinator] {agent_names[agent]} 失败 ✗: {e}")
                agent_results[agent] = f"[生成失败: {e}]"

    # 5. FormatCompliance：格式合规审核
    print(f"\n[FormatCompliance] 🔍 格式合规审核...")
    compliance_results = {}
    for agent, content in agent_results.items():
        if content.startswith("[生成失败"):
            continue
        report = quick_compliance_check(agent, content)
        compliance_results[agent] = report
        status = "✅" if report["compliant"] else "❌"
        print(f"  {agent}: {status} ({report['score']}分, {len(report['issues'])}个问题)")

    # 6. CrossReference：跨章节一致性（简化版）
    print(f"\n[CrossReference] 🔗 跨章节一致性校验...")
    all_content = " ".join(agent_results.values())
    cross_issues = []

    # 检查面积数据一致性
    import re
    hectares = set(re.findall(r'(\d+\.?\d*)\s*公顷', all_content))
    if len(hectares) > 1:
        cross_issues.append(f"面积（公顷）不一致: {hectares}")
        print(f"  ❌ 面积数据不一致: {hectares}")

    mu_values = set(re.findall(r'(\d+\.?\d*)\s*亩', all_content))
    if len(mu_values) > 1:
        mu_floats = [float(v) for v in mu_values]
        if max(mu_floats) - min(mu_floats) > 1:
            cross_issues.append(f"面积（亩）不一致: {mu_values}")
            print(f"  ❌ 亩数不一致: {mu_values}")

    if not cross_issues:
        print("  ✅ 跨章节数据一致性通过")

    # 7. 汇总输出
    all_paragraphs = {}
    for agent, content in agent_results.items():
        if content.startswith("[生成失败"):
            continue
        para_map = split_ai_content_to_paragraphs(content, agent)
        all_paragraphs.update(para_map)

    # 生成协调日志
    thinking_log = {
        "phase": "generation_complete",
        "agents_invoked": list(agent_results.keys()),
        "total_paragraphs_generated": len(all_paragraphs),
        "data_quality_score": data_quality,
        "critical_missing": critical_missing,
        "compliance": compliance_results,
        "cross_reference_issues": cross_issues,
        "agent_stats": {
            agent: {
                "content_length": len(content),
                "paragraphs_mapped": len(split_ai_content_to_paragraphs(content, agent)),
                "compliance_score": compliance_results.get(agent, {}).get("score", -1)
    }
            for agent, content in agent_results.items()
            if not content.startswith("[生成失败")
        }
    }

    result = {
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": LLM_MODEL,
            "template_version": "金湖稳评报告模板 v2026",
            "multi_agent_version": "v3.0",
            "thinking_log": thinking_log
    },
        "basic_info": data.get("basic_info", {}),
        "survey_data": data.get("survey_data", {}),
        "ai_content": {str(k): v for k, v in all_paragraphs.items()},
        "ai_raw_content": agent_results
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*70}")
    print(f"[Coordinator] 全部Agent完成！")
    print(f"  总生成段落: {len(all_paragraphs)}")
    print(f"  数据完整度: {data_quality}分")
    print(f"  合规审核: {sum(1 for r in compliance_results.values() if r['compliant'])}/{len(compliance_results)}通过")
    print(f"  一致性: {len(cross_issues)}个问题")
    print(f"  输出文件: {output_path}")
    print(f"{'='*70}")

    return result


def main():
    if len(sys.argv) < 3:
        print("用法: python multi_agent.py <数据JSON路径> <输出JSON路径>")
        print("\n多Agent协同生成系统 v3.0")
        print("\n协同Agent流水线:")
        print("  DataValidator → KnowledgeAgent → WriterAgent ×3 → FormatCompliance → CrossReference")
        sys.exit(1)

    data_path = sys.argv[1]
    output_path = sys.argv[2]

    if not LLM_API_KEY:
        print("错误: 请设置环境变量 DASHSCOPE_API_KEY")
        sys.exit(1)

    result = run_coordinator(data_path, output_path)

    thinking = result["metadata"]["thinking_log"]
    print(f"\n📊 生成报告:")
    print(f"  模型: {LLM_MODEL}")
    print(f"  段落: {thinking['total_paragraphs_generated']}")
    print(f"  数据: {thinking['data_quality_score']}分")
    for agent, stats in thinking["agent_stats"].items():
        print(f"  {agent}: {stats['content_length']}字符 → {stats['paragraphs_mapped']}段落 (合规:{stats['compliance_score']}分)")


if __name__ == "__main__":
    main()
