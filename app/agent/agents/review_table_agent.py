"""ReviewTableAgent — generates the standalone review table (评审表) from confirmed chapters.

Runs AFTER all 10 chapters are confirmed and quality-reviewed.
Extracts key data from each chapter and synthesizes into the review table format.

The review table (评审表) is the official summary document submitted alongside
the full report. It contains:
- Section 1: Project basic info (from Chapter 1)
- Section 2: Survey/public opinion summary (from Chapter 3)
- Section 3: Pre-measure risk scoring (from Chapter 6)
- Section 4: Post-measure risk scoring (from Chapter 8)
- Section 5: Conclusions and recommendations (from Chapter 9)
- Section 6: Risk mitigation summary (from Chapter 7)
"""

import re
import logging
from typing import Dict, List, Any

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ReviewTableAgent(BaseAgent):
    """Generates the review table (评审表) from all 10 confirmed chapters."""

    name = "ReviewTableAgent"
    description = "从10章已确认内容提取数据生成评审表"
    covered_steps = [17]

    # Mapping: review table section → source chapters + extraction keys
    REVIEW_SECTIONS = [
        {
            "name": "项目基本情况",
            "source_chapters": [1],
            "extract_keys": [
                "决策名称", "责任单位", "实施单位", "项目位置",
                "征收面积", "土地用途", "资金测算",
                # 🔴 江苏省统一制式评审表：决策概况 7 条 + 责任/实施单位联系人信息
                "地块现状", "周边安置", "手续办理", "资金筹措", "实施周期", "法律政策依据",
                "责任单位负责人", "责任单位负责人职务", "责任单位联系人", "责任单位联系电话",
                "实施单位联系人", "实施单位联系电话",
            ]
    },
        {
            "name": "公众参与及意见调查情况",
            "source_chapters": [3],
            "extract_keys": [
                "调查方式", "调查人数", "支持人数", "反对人数",
                "支持率", "主要诉求", "基层组织意见", "部门意见", "利益相关者",
            ]
    },
        {
            "name": "风险因素识别情况",
            "source_chapters": [5],
            "extract_keys": [
                "风险因素", "风险等级", "发生概率", "影响程度",
            ]
    },
        {
            "name": "措施前风险等级评估",
            "source_chapters": [6],
            "extract_keys": [
                "合法性得分", "合理性得分", "可行性得分", "可控性得分",
                "措施前总分", "措施前风险等级",
            ]
    },
        {
            "name": "风险防范化解措施",
            "source_chapters": [7],
            "extract_keys": [
                "措施名称", "责任主体", "完成时限",
            ]
    },
        {
            "name": "确定风险等级",
            "source_chapters": [8],
            "extract_keys": [
                "措施后总分", "得分变化", "措施后风险等级",
            ]
    },
        {
            "name": "评估结论与建议",
            "source_chapters": [9],
            "extract_keys": [
                "综合结论", "风险等级", "工作建议",
            ]
    },
    ]

    async def think(self, state: dict) -> Dict[str, Any]:
        """Plan the review table generation."""
        chapters = state.get("chapters", {})
        confirmed_count = sum(
            1 for ch_num, ch in chapters.items()
            if isinstance(ch, dict) and ch.get("status") == "approved"
        )

        steps = [
            f"📋 从已确认 {confirmed_count}/10 章中提取评审数据...",
            "📊 提取项目基本信息（第1章）...",
            "📊 提取公众意见调查数据（第3章）...",
            "📊 提取风险评分数据（第5-8章）...",
            "📊 提取评估结论（第9章）...",
            "📝 合成评审表Markdown...",
        ]

        if confirmed_count < 10:
            steps.insert(0, f"⚠️ 仅 {confirmed_count}/10 章已确认，评审表可能不完整")

        return {
            "summary": f"从已确认章节提取数据生成评审表（{confirmed_count}/10章）",
            "steps": steps,
            "actions": [{"type": "extract_and_synthesize"}],
            "confirmed_count": confirmed_count
    }

    async def act(self, state: dict, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Extract data from chapters and synthesize the review table."""
        await self._emit_review_table_start()

        chapters = state.get("chapters", {})
        generated = state.get("generated_sections", {})
        filled = state.get("filled_data", {})

        # Extract data from each section
        extracted = {}
        for section in self.REVIEW_SECTIONS:
            section_name = section["name"]
            section_data = {}

            for ch_num in section["source_chapters"]:
                ch_data = chapters.get(ch_num, {})
                if not isinstance(ch_data, dict):
                    continue
                markdown = ch_data.get("markdown", "")

                for key in section["extract_keys"]:
                    value = self._extract_value(markdown, key)
                    if value:
                        section_data[key] = value

            # 🔴 Fallback: extract from chapter data packages and filled_data
            for key in section["extract_keys"]:
                if key not in section_data or not section_data[key]:
                    # Try chapter data packages
                    for ch_num in section["source_chapters"]:
                        ch_pkg = (state.get("_chapter_data_packages") or {}).get(ch_num, {})
                        if isinstance(ch_pkg, dict) and key in ch_pkg:
                            section_data[key] = str(ch_pkg[key])[:200]
                            break
                if key not in section_data or not section_data[key]:
                    if key in filled:
                        section_data[key] = str(filled[key])[:200]
                # Try generated_sections markdown
                if key not in section_data or not section_data[key]:
                    for ch_num in section["source_chapters"]:
                        gen = generated.get(f"chapter_{ch_num}", {})
                        if isinstance(gen, dict):
                            val = self._extract_value(gen.get("markdown", ""), key)
                            if val:
                                section_data[key] = val
                                break

            extracted[section_name] = section_data

        # 🔴 决策概况 7 条：用 LLM 从正文/预公告/勘测定界归纳（照抄/扩写正文内容）
        try:
            _gk = await self._summarize_decision_overview(state, extracted)
            if _gk:
                extracted.setdefault("项目基本情况", {}).update(_gk)
        except Exception as e:
            logger.warning(f"决策概况归纳失败: {e}")

        # Build review table as Markdown
        review_md = self._build_review_markdown(extracted, state)

        # 🔴 生成评审表 DOCX（江苏省统一制式 14 栏）
        review_table_path = None
        try:
            import os as _os
            from app.services.review_table_service import generate_review_table
            from app.config import settings as _settings
            _sid = str(state.get("session_id", "report"))[:12]
            _out = str(_settings.STORAGE_DIR / "generated" / f"评审表_{_sid}.docx")
            _os.makedirs(_os.path.dirname(_out), exist_ok=True)
            review_table_path = generate_review_table(
                _out, state.get("filled_data", {}) or {}, state.get("chapters", {}) or {},
                extracted=extracted)
        except Exception as e:
            logger.warning(f"评审表 DOCX 生成失败: {e}")

        # Emit the review table content
        await self._emit_review_table(review_md, review_table_path)

        # Emit completion
        await self._emit_review_table_complete(review_table_path)

        return {
            "markdown": review_md,
            "extracted_data": extracted,
            "review_table_path": review_table_path,
            "sections_count": len(extracted)
    }

    async def validate(self, result: Dict[str, Any]) -> List[str]:
        """Validate the review table."""
        issues = []
        extracted = result.get("extracted_data", {})
        markdown = result.get("markdown", "")

        # Check each section has data
        for section_name, data in extracted.items():
            if not data or len(data) == 0:
                issues.append(f"评审表「{section_name}」章节未提取到数据")

        if len(markdown) < 500:
            issues.append("评审表内容过短（<500字）")

        return issues

    async def update_state(self, state: dict, result: Dict[str, Any]) -> dict:
        """Store review table data in state."""
        state["_review_table_markdown"] = result.get("markdown", "")
        state["_review_table_extracted"] = result.get("extracted_data", {})
        state["review_table_path"] = result.get("review_table_path")

        # Also store in generated_sections for assembly
        state.setdefault("generated_sections", {})["review_table"] = {
            "title": "评审表",
            "markdown": result.get("markdown", "")
    }

        return state

    # ---- Internal Methods ----

    def _extract_value(self, markdown: str, key: str) -> str:
        """Extract a specific value from markdown text by key name.

        Uses multi-strategy extraction: key-value pairs, table cells, paragraphs.
        """
        if not markdown:
            return ""

        # Strategy 1: Direct key-value pattern (plain + bold)
        patterns = [
            rf'(?:^|\n)\s*\*?\*?{key}\*?\*?\s*[：:]\s*(.+?)(?:\n|$)',
            rf'\|\s*\*?\*?{key}\*?\*?\s*\|\s*(.+?)\s*\|',  # Table cell
            rf'{key}\s*[是为即]\s*(.+?)(?:\n|[，。；])',
        ]

        for pattern in patterns:
            match = re.search(pattern, markdown)
            if match:
                value = match.group(1).strip()
                # Clean up
                value = re.sub(r'[【［].*?[】］]', '', value)
                value = re.sub(r'\*\*', '', value)  # Remove bold markers
                value = value.strip('，。；：、 |')
                if len(value) > 1:
                    return value[:200]

        # Strategy 2: Search in table rows (| key | value | format)
        for line in markdown.split('\n'):
            if '|' not in line:
                continue
            cells = [c.strip() for c in line.split('|') if c.strip()]
            if len(cells) >= 2:
                for i, cell in enumerate(cells):
                    clean_cell = re.sub(r'\*\*', '', cell)
                    if key in clean_cell and i + 1 < len(cells):
                        val = re.sub(r'\*\*', '', cells[i + 1])
                        val = re.sub(r'[【［].*?[】］]', '', val)
                        if len(val.strip()) > 1:
                            return val.strip()[:200]

        # Strategy 3: Keyword aliases (different naming conventions)
        aliases = {
            "决策名称": ["项目名称", "报告标题", "决策事项"],
            "责任单位": ["征收主体", "决策单位", "实施主体", "组织单位"],
            "实施单位": ["稳评单位", "评估单位"],
            "项目位置": ["征收位置", "坐落", "地理位置", "拟征地位置"],
            "征收面积": ["面积", "用地面积", "拟征收土地面积"],
            "调查方式": ["调查方法", "公众参与方式"],
            "调查人数": ["样本数", "参与人数", "受访人数"],
            "支持人数": ["赞成人数", "同意人数"],
            "支持率": ["赞成率", "同意率", "满意度"],
            "风险等级": ["综合风险等级", "评估等级"]
    }
        for alias in aliases.get(key, []):
            result = self._extract_value(markdown, alias)
            if result:
                return result

        # Strategy 4: For numeric fields, search broadly
        if any(kw in key for kw in ["面积", "人数", "得分", "总分", "率"]):
            num_patterns = {
                "面积": [r'(?:面积|占地)[^\d]*(\d[\d,.]*\s*(?:㎡|平方米|亩))',
                         r'(\d{5,7})\s*(?:㎡|平方米)',
                         r'(\d+\.?\d*)\s*亩'],
                "人数": [r'(\d+)\s*(?:人|户|份)'],
                "得分": [r'(\d{1,2})\s*分'],
                "率": [r'(\d{2,3}\.?\d*)\s*%']
    }
            for kw_part, pats in num_patterns.items():
                if kw_part in key:
                    for pat in pats:
                        m = re.search(pat, markdown)
                        if m:
                            return m.group(0)[:100]

        return ""

    async def _summarize_decision_overview(self, state: dict, extracted: dict) -> dict:
        """用 LLM 从正文/预公告/勘测定界归纳「决策概况」7 条（照抄/扩写正文，不编造）。"""
        import json
        chapters = state.get("chapters", {}) or {}
        text = "\n".join(
            (ch.get("markdown", "") if isinstance(ch, dict) else str(ch))
            for ch in chapters.values()
        )
        if len(text) < 200:
            return {}
        prompt = (
            "你是稳评报告审核专家。请根据下面报告正文，归纳「决策概况」的 7 个方面，"
            "每条 80 字以内，内容直接照抄/扩写正文，不得编造：\n"
            "1.决策地理位置 2.决策用地范围、规模及地上附着物情况 3.地块现状及周边情况 "
            "4.周边地块安置情况 5.相关手续办理情况 6.资金筹措 7.决策实施周期\n"
            "🔴 各条要点：\n"
            "- 1.只写地理位置（街道/社区/村组），不要把面积、用途也塞进来；\n"
            "- 2.只写用地范围与规模（面积、权属、用途）与地上附着物包含哪些；\n"
            "- 3.写**现状地类与周边**（如「以林地、耕地和水域及水利设施用地为主…周边以农村"
            "居民点和农用地为主」），**不要**写成四至/红线图；\n"
            "- 4.写**安置**，句式仿制式模板：「根据和<社区/村>负责人沟通了解：周边地块原安置"
            "方式是按照『征谁补谁』原则，优先从承包户中确定社会保障名额；本决策拟征收土地为"
            "集体土地，被征地农民的安置按《江苏省被征地农民社会保障办法》…执行」；\n"
            "- 5.写预公告的发布与公告期限；6.写资金来源与渠道；7.写实施周期起止。\n"
            "只输出 JSON，键固定为：{\"决策地理位置\":\"\",\"规模附着物\":\"\",\"地块现状\":\"\","
            "\"周边安置\":\"\",\"手续办理\":\"\",\"资金筹措\":\"\",\"实施周期\":\"\"}\n\n"
            f"报告正文（节选）：\n{text[:10000]}"
        )
        try:
            from app.services.llm_service import LLMService
            resp = await LLMService().chat(messages=[{"role": "user", "content": prompt}],
                                           max_tokens=1500, temperature=0.2)
            content = resp if isinstance(resp, str) else (resp.get("content", "") if isinstance(resp, dict) else str(resp))
            m = re.search(r'\{[\s\S]*\}', content or "")
            if not m:
                return {}
            data = json.loads(m.group())
            return {k: str(data[k]).strip()[:200] for k in
                    ("决策地理位置", "规模附着物", "地块现状", "周边安置", "手续办理", "资金筹措", "实施周期")
                    if data.get(k)}
        except Exception as e:
            logger.warning(f"决策概况 LLM 归纳异常: {e}")
            return {}

    async def _summarize_opinions(self, state: dict) -> dict:
        """用 LLM 从正文归纳「基层组织意见」与「党委政府或相关部门意见」。

        🔴 这两栏规则抽取时常落空（关键词对不上动态小节名），印出来就是「【待填写】」，
           评审专家看到的是个空栏目。改由 agent 从正文归纳 —— **只能依据正文**，
           正文里没有的部门/表态一律不写（业主：「不要胡编乱造」）。
        """
        import json
        chapters = state.get("chapters", {}) or {}
        text = "\n".join(
            (ch.get("markdown", "") if isinstance(ch, dict) else str(ch))
            for ch in chapters.values()
        )
        if len(text) < 200:
            return {}
        prompt = (
            "你是稳评报告审核专家。请根据下面报告正文，归纳评审表的两栏内容：\n"
            "①「基层组织意见」：拟征收土地所在地的乡镇/街道办事处、社区（村）居委会等"
            "基层组织对本决策的表态与要求。\n"
            "②「党委政府或相关部门意见」：所在区（县）党委政府及相关职能部门（自然资源、"
            "财政、人社、信访等）的意见。\n"
            "要求：\n"
            "- 每条 100~200 字，用「xxx（单位）：……」的写法分别列出；\n"
            "- **只能依据正文**：正文里没出现的单位、没表达过的表态，一律不要写；\n"
            "- 正文若只笼统写了「相关单位均表示支持」，就据此概括，不要补具体部门名；\n"
            "- 只输出 JSON，键固定为：{\"基层组织意见\":\"\",\"党委政府或相关部门意见\":\"\"}\n\n"
            f"报告正文（节选）：\n{text[:12000]}"
        )
        try:
            from app.services.llm_service import LLMService
            resp = await LLMService().chat(messages=[{"role": "user", "content": prompt}],
                                           max_tokens=1200, temperature=0.2)
            content = resp if isinstance(resp, str) else (resp.get("content", "") if isinstance(resp, dict) else str(resp))
            m = re.search(r'\{[\s\S]*\}', content or "")
            if not m:
                return {}
            data = json.loads(m.group())
            return {k: str(data[k]).strip()[:400] for k in
                    ("基层组织意见", "党委政府或相关部门意见") if data.get(k)}
        except Exception as e:
            logger.warning(f"各方意见 LLM 归纳异常: {e}")
            return {}

    def _build_review_markdown(
        self, extracted: Dict[str, Dict[str, str]], state: dict
    ) -> str:
        """按江苏省统一制式评审表结构（14 栏）生成 Markdown。

        栏目：决策事项名称 / 适用程序 / 稳评责任单位 / 稳评实施单位 / 参与评估单位 /
        决策概况（7条）/ 决策法律政策依据及必要性 / 利益相关者情况 / 各方意见 /
        主要风险因素 / 降低风险措施 / 稳评实施单位结论 / 评审意见 / 稳评结论 / 备案情况。
        """
        title = state.get("report_title", "社会稳定风险评估报告")
        filled = state.get("filled_data", {}) or {}
        basic = extracted.get("项目基本情况", {})
        survey = extracted.get("公众参与及意见调查情况", {})
        risk_id = extracted.get("风险因素识别情况", {})
        pre_score = extracted.get("措施前风险等级评估", {})
        measures = extracted.get("风险防范化解措施", {})
        conclusions = extracted.get("评估结论与建议", {})

        def _g(d, k, default="【待补充】"):
            v = (d or {}).get(k, "")
            return str(v).strip() if v else default

        _name = _g(basic, "决策名称", filled.get("project_name") or filled.get("doc_reference") or "【待补充】")
        _org = _g(basic, "责任单位", filled.get("org_name") or "【待补充】")
        # 🔴 不再回落到写死的「江苏众拓…」—— 实施单位来自 *本报告所属公司*，
        #    没有公司就留待填写（外部用户的报告抬头不能被印成我们的）。
        _imp = _g(basic, "实施单位", filled.get("implement_unit") or "【待填写】")
        _loc = _g(basic, "项目位置", filled.get("location") or "")
        _area = _g(basic, "征收面积", filled.get("area_mu") or filled.get("area_m2") or "")
        _use = _g(basic, "土地用途", filled.get("land_use") or "")
        # 责任单位/实施单位联系人信息需用户提供（缺失留待补充）
        _r_head = _g(basic, "责任单位负责人", filled.get("org_leader", "【待补充】"))
        _r_title = _g(basic, "责任单位负责人职务", filled.get("org_leader_title", "【待补充】"))
        _r_contact = _g(basic, "责任单位联系人", filled.get("org_contact", "【待补充】"))
        _r_phone = _g(basic, "责任单位联系电话", filled.get("org_phone", "【待补充】"))
        # 🔴 实施单位的联系人**不再默认「安如月」** —— 那是我们公司的人，
        #    外部用户出报告会印上去。取不到就留占位。
        _i_contact = _g(basic, "实施单位联系人", filled.get("impl_contact", "【待填写】"))
        _i_phone = _g(basic, "实施单位联系电话", filled.get("impl_phone", "【待补充】"))
        # 🔴 实施单位负责人/职务：原先在这里**写死「陈春 / 法人」**（第 457 行）。
        #    多租户下每家公司的负责人不同，值应由**公司档案**按账号归属提供
        #    （`companies.leader_name / leader_title` → 工作流注入 filled_data）。
        _i_head = _g(basic, "实施单位负责人", filled.get("impl_leader", "【待填写】"))
        _i_title = _g(basic, "实施单位负责人职务", filled.get("impl_leader_title", "【待填写】"))

        # 决策概况 7 条（多数直接取自正文/预公告/勘测定界）
        _gk = {
            "1.决策地理位置": _g(basic, "决策地理位置", _loc or "【待补充】"),
            "2.决策用地范围、规模及地上附着物情况": _g(basic, "规模附着物", (f"拟征收面积{_area}，土地用途{_use}。" if _area or _use else "【待补充】")),
            "3.地块现状及周边情况": _g(basic, "地块现状", "【待补充】"),
            "4.周边地块安置情况": _g(basic, "周边安置", "【待补充】"),
            "5.相关手续办理情况": _g(basic, "手续办理", "【待补充】"),
            "6.资金筹措": _g(basic, "资金筹措", filled.get("funding") or "【待补充】"),
            "7.决策实施周期": _g(basic, "实施周期", filled.get("solicitation_period") or "自拟征地公告发布之日起至征地工作结束"),
        }
        _gk_text = "  ".join(f"{k} {v}" for k, v in _gk.items())

        rows = [
            ("决策事项名称", _name),
            ("适用程序", "□简易程序　☑一般程序　□特殊程序"),
            ("稳评责任单位", f"单位名称：{_org}；负责人：{_r_head}；职务：{_r_title}；联系人：{_r_contact}；联系电话：{_r_phone}"),
            ("稳评实施单位", f"单位名称：{_imp}；负责人：{_i_head}；职务：{_i_title}；联系人：{_i_contact}；联系电话：{_i_phone}"),
            ("参与评估单位", "/"),
            ("决策概况", _gk_text),
            ("决策法律政策依据及必要性", _g(basic, "法律政策依据", "【待从第2章提取】")),
            ("利益相关者情况", _g(basic, "利益相关者", "【待从第1/3章提取】")),
            ("各方意见", "利益相关者诉求：" + _g(survey, "主要诉求", "【待补充】")
                        + "；基层组织意见：" + _g(survey, "基层组织意见", "【待补充】")
                        + "；党委政府或相关部门意见：" + _g(survey, "部门意见", "【待补充】")),
            ("主要风险因素", _g(risk_id, "风险因素", "【待从第5章提取】")),
            ("降低风险措施", _g(measures, "措施名称", "【待从第7章提取】")),
            ("稳评实施单位结论", _g(conclusions, "综合结论", "【待从第9章提取】")),
            ("评审意见", "组长（签章）：　　　　年　　月　　日"),
            ("稳评结论", "负责人（签章）：　　　　年　　月　　日"),
            ("备案情况", "备案单位（签章）：　　　　年　　月　　日"),
        ]

        parts = [f"# {title}", "", "## 社会稳定风险评估评审表", ""]
        parts.append("| 栏目 | 内容 |")
        parts.append("|------|------|")
        for k, v in rows:
            parts.append(f"| **{k}** | {v} |")
        parts.append("")
        return "\n".join(parts)

    async def _emit_review_table_start(self) -> None:
        """Emit review_table_start event."""
        if self._stream_queue:
            await self._stream_queue.put({
                "event": "review_table_start",
                "data": {"message": "正在从已确认的10章内容中提取数据生成评审表..."}
    })

    async def _emit_review_table(
        self, markdown: str, docx_path: str = None
    ) -> None:
        """Emit the review table content."""
        if self._stream_queue:
            await self._stream_queue.put({
                "event": "message",
                "data": {
                    "role": "agent",
                    "content": markdown,
                    "message_type": "review_table"
    }
    })

    async def _emit_review_table_complete(self, docx_path: str = None) -> None:
        """Emit review_table_complete event."""
        if self._stream_queue:
            data = {"message": "评审表生成完成"}
            if docx_path:
                data["path"] = docx_path
                # Build download URL
                import os
                filename = os.path.basename(docx_path)
                data["download_url"] = f"/api/v1/history/reports/by-path/{filename}"

            await self._stream_queue.put({
                "event": "review_table_complete",
                "data": data
    })
