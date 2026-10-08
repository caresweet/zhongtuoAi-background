"""座谈会 PDF 问卷勾选解析 —— 从扫描件**逐页识别勾选项并汇总**。

背景（业主口径）：
    「问卷调查分析是依据座谈会 PDF 中的图表计算的 —— 群众调查分析和部门调查分析。」

即：报告第9章「问卷调查情况」与表9-1/9-2、附件2「问卷调查统计分析」、
以及评分体系里的支持率，都应当**由这份扫描件算出来**，而不是人工核验后写死在代码里。

两类表：
  · 群众问卷《稳评问卷调查表》—— 每人 1 页，六维勾选（题面为**调查表第一列原文**）：
      您是 / 您的年龄 / 您的职业 / 您是否了解本决策 / 对该决策是否支持 / 您会采取何种方式解决诉求
  · 部门问卷《社会稳定风险评估单位（部门）问卷调查表》—— 每单位 1 页，7 问：
      了解程度 / 宣传公示满意度 / 补偿安置政策了解 / 关心的主要事项（多选）/
      社会稳定风险等级 / 保持稳定信心 / 基本态度

实现：逐页渲染 → 视觉 LLM 按**固定选项表**识别被勾选项 → 汇总。
结果按「页图 md5」缓存到磁盘，跨 session 复用（重跑不重复烧 token）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import pickle
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 两张表的固定选项（识别时只在这些选项里选，避免模型自由发挥）
# ═══════════════════════════════════════════════════════════════

PUBLIC_QUESTIONS: Dict[str, List[str]] = {
    "identity":    ["本地居民", "租住本区", "附近上班", "路过"],
    "age":         ["16～35", "36～55", "56以上"],
    "occupation":  ["机关事业", "企业", "待业", "其他"],
    "awareness":   ["了解", "不了解"],
    "attitude":    ["支持", "无所谓", "有条件支持", "反对", "强烈反对"],
    "resolution":  ["调解", "诉讼", "正常反映", "其他"],
}

# 🔴 群众问卷六道题的**题面原文**（照抄《稳评问卷调查表》表格第一列，逐字一致）。
#    报告表9-1/表3-1「调查内容」列、第9章正文与事实卡分项标题都以这里为唯一事实源
#    —— 评审时报告统计表的题目必须与实际发放的问卷一致，不得用概括改写
#    （业主实测挑刺：「您的身份」应作「您是」、「是否了解本决策」应作「您是否了解本决策」）。
PUBLIC_QUESTION_TEXTS: Dict[str, str] = {
    "identity":    "您是",
    "age":         "您的年龄",
    "occupation":  "您的职业",
    "awareness":   "您是否了解本决策",
    "attitude":    "对该决策是否支持",
    "resolution":  "您会采取何种方式解决诉求",
}

DEPT_QUESTIONS: Dict[str, List[str]] = {
    "awareness":               ["了解", "了解一些", "不了解"],
    "publicity_satisfaction":  ["很满意", "满意", "基本满意", "不满意"],
    "policy_understanding":    ["了解", "了解一些", "不了解"],
    "concerns":                ["征地用途", "征地范围", "补偿费用", "土地换社保", "其他"],  # 多选
    "risk_assessment":         ["高风险", "中风险", "低风险"],
    "stability_confidence":    ["有信心", "在上级党委政府的支持下比较有信心", "不确定", "无信心"],
    "support_attitude":        ["支持", "无所谓", "反对"],
}

# 🔴 部门问卷七问的**题面原文**（照抄《社会稳定风险评估单位（部门）问卷调查表》题干，
#    逐字一致）。业主评审要求统计表题目与实际发放问卷一致，不得概括改写
#    （实测原件：q3 是「贵单位对淮安市征地补偿安置政策了解？」——不是
#    「…补偿安置政策的了解程度？」；q4 括注是「多选」不是「可多选」）。
#    `{location}` 占位符由表格服务用材料里的市/县名替换（取不到回退「淮安市」）。
DEPT_QUESTION_TEXTS: Dict[str, str] = {
    "awareness":               "贵单位对本决策的了解程度如何？（单选）",
    "publicity_satisfaction":  "贵单位对政府的征地宣传、公示等工作是否满意？（单选）",
    "policy_understanding":    "贵单位对{location}征地补偿安置政策了解？（单选）",
    "concerns":                "贵单位所关心的决策主要事项是？（多选）",
    "risk_assessment":         "贵单位或部门认为本决策实施的社会稳定风险等级是？（单选）",
    "stability_confidence":    "在本决策实施全过程中，贵单位或部门对内部保持稳定是否有信心？（单选）",
    "support_attitude":        "贵单位对本决策实施的基本态度是？（单选）",
}

# 汇总时字段 → 中文标题（题面唯一事实源 = PUBLIC_QUESTION_TEXTS，与调查表第一列逐字一致）
_GROUP_TITLES = {
    "identity_groups": PUBLIC_QUESTION_TEXTS["identity"],
    "age_groups": PUBLIC_QUESTION_TEXTS["age"],
    "occupation_groups": PUBLIC_QUESTION_TEXTS["occupation"],
    "awareness_levels": PUBLIC_QUESTION_TEXTS["awareness"],
    "attitude_options": PUBLIC_QUESTION_TEXTS["attitude"],
    "resolution_methods": PUBLIC_QUESTION_TEXTS["resolution"],
}


def _vision_prompt() -> str:
    def _opts(d: Dict[str, List[str]], title: str) -> str:
        lines = [f"【{title}】"]
        for k, opts in d.items():
            lines.append(f"  {k}: " + " / ".join(opts))
        return "\n".join(lines)

    return f"""这是一份社会稳定风险评估调查问卷的扫描件（可能是「群众问卷」或「部门问卷」，也可能两者都不是）。

请**只依据图片中被勾选（打√、打×、画圈或明显笔迹标记）的选项**作答，没勾的一律不要猜。

判断表种：标题含「稳评问卷调查表」= 群众问卷(public)；
标题含「社会稳定风险评估单位（部门）问卷调查表」= 部门问卷(dept)；
两者都不是（如签到表、会议记录、公告）= none。

🔴 同时抄录**手写日期**：问卷/表格的「日期」栏、「时间」栏、「被调查日期」等处的
   **手写笔迹**（这是调查实际发生的时间，报告里要用），归一成 YYYY-MM-DD。

群众问卷按这六项识别：
{_opts(PUBLIC_QUESTIONS, "群众问卷")}

部门问卷按这七项识别（其中 concerns 是多选，可能勾多个）：
{_opts(DEPT_QUESTIONS, "部门问卷")}

严格输出 JSON（不要解释、不要代码块围栏）：
{{
  "form_type": "public" | "dept" | "none",
  "form_date": "该页上**手写**的日期，形如 2026-04-28；只有年月就补 01；看不清/没有就空串",
  "public": {{"identity": "", "age": "", "occupation": "", "awareness": "", "attitude": "", "resolution": ""}},
  "dept": {{"awareness": "", "publicity_satisfaction": "", "policy_understanding": [], "concerns": [], "risk_assessment": "", "stability_confidence": "", "support_attitude": ""}}
}}

要求：
1. 取值必须**严格等于**上面列出的某个选项文本（含全角/半角写法按原表），识别不出就填空字符串或空数组；
2. dept 的 policy_understanding 和 concerns 是**数组**（多选）；其余为单个字符串（单选）；
3. form_type=none 时 public/dept 全填空；
4. 4. 🔴 `form_date` **只抄手写**的日期（印刷的模板样本日期、空白栏不要）；
   常见写法「2026.4.28」「2026年4月30日」「2026/4/28」都要归一成 2026-04-28。

🔴🔴 手写年份极易看错（「26」看成「16」「76」等）。抄写前请**逐个数字确认**：
    · 本批资料都是**近几年**的稳评项目（2024~2027 年之间）；得出的年份若不在这个范围，
      说明读错了，请重新看图确认。
    · 年份只写了两位（如「26.4.28」）时，补成 **2026**，不要补成 2016。
    · **拿不准就填空串**，绝对不要猜一个年份出来。
"""


def _norm_date(v: Any) -> str:
    """把手写日期的各种写法归一成 `YYYY-MM-DD`；识别不出返回空串。

    覆盖实测出现的写法：`2026.4.28`、`2026年4月30日`、`2026/04/28`、`2026-4-28`、
    `2026年4月`（补 01）。**不做任何推断** —— 识别不到就返回空串，
    报告里宁可写规范话术，也不要一个编出来的日期。
    """
    s = str(v or "").strip()
    if not s:
        return ""
    m = re.search(r'(\d{4})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})', s)
    if not m:
        # 「2026 4 28」这种空格分隔（手写常见：年月日之间只空一格）
        m = re.search(r'(\d{4})\s+(\d{1,2})\s+(\d{1,2})(?!\d)', s)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
    else:
        m = re.search(r'(\d{4})\s*[年./\-]\s*(\d{1,2})\s*月?', s)
        if m:
            y, mo, d = m.group(1), m.group(2), "1"
        else:
            # 🔴 手写「26.4.28」——年份只写两位。按 20XX 补全
            #    （00-79 → 20xx；80-99 → 19xx）。视觉模型有时会把「26」
            #    补成错的四位数（实测把 2026 读成 2016），所以两位写法
            #    反而更可信，务必支持。
            m = re.search(r'(?<!\d)(\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})(?!\d)', s)
            if not m:
                return ""
            _yy = int(m.group(1))
            y = str(2000 + _yy) if _yy <= 79 else str(1900 + _yy)
            mo, d = m.group(2), m.group(3)
    try:
        yi, moi, di = int(y), int(mo), int(d)
        if not (1 <= moi <= 12 and 1 <= di <= 31):
            return ""
        return f"{yi:04d}-{moi:02d}-{di:02d}"
    except (TypeError, ValueError):
        return ""


def _filter_dates(dates: List[str]) -> List[str]:
    """清理手写日期集合：**先纠年份，再剔异常**，返回升序结果。

    为什么需要它：手写「26」被视觉模型读成「2016」时，`_pub_dates[0]`
    就会变成 2016-04-28 —— 报告正文会写成「2016年4月28日至4月30日」
    这种**年份穿越**的句子（实测发生过）。

    做法（保守、可解释）：
      ① 时效性硬过滤：年份必须落在 `2000 ~ 今年+1`，明显不合理直接丢；
      ② 取**众数年份**为主体（并列取较新的年份）；
      ③ 与主体年份相差 **>1 年** 的样本分两种：
         · 若它的「月-日」落在主体日期区间 ±7 天内 → 认定为**同年误读**，
           只把年份改回主体年份（保留月日，信息不丢）；
         · 否则视为不可信，丢弃。
         （±1 年以内的样本一律保留，兼容「12月→次年1月」的跨年调查。）

    全部被过滤掉时**退回原集合** —— 宁可留一个可疑日期让用户看见，
    也不要静默变成「无日期」。
    """
    _valid = [d for d in (dates or []) if re.match(r'^\d{4}-\d{2}-\d{2}$', str(d or ""))]
    if not _valid:
        return []
    import datetime as _dt
    _hi = _dt.date.today().year + 1
    _stage1 = [d for d in _valid if 2000 <= int(d[:4]) <= _hi]
    _base = _stage1 or _valid
    from collections import Counter
    _years = Counter(int(d[:4]) for d in _base)
    if len(_years) == 1:
        return sorted(set(_base))
    # 众数年份；并列时取较**新**的年份（报告场景下 2026 比 2016 更可能是真的）
    _main_year = max(_years.items(), key=lambda kv: (kv[1], kv[0]))[0]
    _near = sorted({d for d in _base if abs(int(d[:4]) - _main_year) <= 1})
    if not _near:                                   # 理论上不会发生
        return sorted(set(_base))

    def _md(iso: str) -> _dt.date:
        return _dt.date(2000, int(iso[5:7]), int(iso[8:10]))

    _lo, _hi_md = min(_md(d) for d in _near), max(_md(d) for d in _near)
    _pad = _dt.timedelta(days=7)
    _out, _fixed, _dropped = list(_near), [], []
    for d in _base:
        if d in _near:
            continue
        _cand_md = _md(d)
        if _lo - _pad <= _cand_md <= _hi_md + _pad:      # 月日落在主体区间 → 只错年份
            _rep = f"{_main_year:04d}-{d[5:]}"
            if _rep not in _out:
                _out.append(_rep)
                _fixed.append(f"{d}→{_rep}")
        else:
            _dropped.append(d)
    if _fixed or _dropped:
        logger.info(f"手写日期清理: 主体年={_main_year}, 纠正={_fixed}, 丢弃={_dropped}")
    return sorted(set(_out)) or sorted(set(_base))


def _cn_date(iso: str) -> str:
    """`2026-04-28` → `2026年4月28日`（识别不出原样返回）。"""
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', str(iso or ""))
    if not m:
        return str(iso or "")
    return f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日"


def _cn_range(a: str, b: str) -> str:
    """两个 ISO 日期 → 中文区间：同年写「2026年4月28日至4月30日」，同日写单日。"""
    if not a:
        return ""
    if not b or a == b:
        return _cn_date(a)
    ma = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', a)
    mb = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', b)
    if not (ma and mb):
        return f"{_cn_date(a)}至{_cn_date(b)}"
    if ma.group(1) == mb.group(1):
        return (f"{int(ma.group(1))}年{int(ma.group(2))}月{int(ma.group(3))}日"
                f"至{int(mb.group(2))}月{int(mb.group(3))}日")
    return f"{_cn_date(a)}至{_cn_date(b)}"


def _cache_dir() -> Path:
    from app.config import settings
    d = settings.STORAGE_DIR / "questionnaire_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


# 🔴 解析缓存版本 —— **改了视觉 prompt 或汇总结构就必须 +1**：
#    否则旧的页缓存（`*.pkl`）与整份汇总（`summary_*.json`）会被命中，
#    新识别的字段（如本次新增的**手写日期 form_date**）永远拿不到值
#    ——表现为「代码改了，报告里还是没日期」。
#    v1 → v2：新增手写日期识别 + 问卷题面口径。
#    v2 → v3：日期 prompt 加「年份逐个确认」约束 + `_norm_date` 两位年份
#             + `_filter_dates` 纠正/剔除误读年份（实测「2026」被读成「2016」）。
_CACHE_VERSION = 3


def _page_cache_key(pdf_path: str, page_index: int) -> str:
    h = hashlib.md5()
    h.update(str(_CACHE_VERSION).encode())
    h.update(str(pdf_path).encode())
    h.update(str(os.path.getmtime(pdf_path)).encode())
    h.update(str(page_index).encode())
    return h.hexdigest()


def _pdf_fingerprint(pdf_path: str) -> str:
    """PDF 路径 + mtime + 页数 的指纹（整份汇总结果按此缓存）。"""
    h = hashlib.md5()
    h.update(str(pdf_path).encode())
    try:
        h.update(str(os.path.getmtime(pdf_path)).encode())
        import fitz
        _d = fitz.open(pdf_path); h.update(str(len(_d)).encode()); _d.close()
    except Exception:
        pass
    return h.hexdigest()


def summary_cache_path(pdf_path: str) -> Path:
    return _cache_dir() / f"summary_{_pdf_fingerprint(pdf_path)}.json"


# 🔴 印刷体选项归一：视觉识别常给年龄选项多带一个「岁」（原件印的是「16～35」无「岁」），
#    或把「56 以上」识别成「56以上/56岁以上」。统计表选项必须与问卷**印刷原件逐字一致**
#    （业主评审核验点），因此在聚合出口与消费出口统一归一 —— 用正则提取数字，
#    不依赖模型措辞，新旧缓存都能纠正。
_AGE_RANGE_RE = re.compile(r'(\d+)\s*[~～\-—－至]\s*(\d+)')
_AGE_ABOVE_RE = re.compile(r'(\d+)\s*岁?\s*以[上内]?')


def canon_age_option(v: str) -> str:
    """年龄选项归一成问卷印刷体：「16～35」「36～55」「56 以上」。"""
    s = str(v or "").strip()
    m = _AGE_RANGE_RE.search(s)
    if m:
        return f"{m.group(1)}～{m.group(2)}"
    m = _AGE_ABOVE_RE.search(s)
    if m:
        return f"{m.group(1)} 以上"
    return s


def canon_public_groups(dim: str, groups: Any) -> Dict[str, Any]:
    """把某维度的分组键归一成问卷印刷体选项（目前只有年龄需要）。

    同义键（「36～55」/「36～55岁」）的计数会**合并**，不会丢样本。
    """
    if not isinstance(groups, dict) or not groups:
        return groups if isinstance(groups, dict) else {}
    if dim != "age":
        return groups
    out: Dict[str, Any] = {}
    for k, v in groups.items():
        ck = canon_age_option(k)
        try:
            out[ck] = out.get(ck, 0) + int(v or 0)
        except (TypeError, ValueError):
            out[ck] = out.get(ck, 0)
    return out


def load_cached_summary(pdf_path: str) -> Dict[str, Any]:
    """读取已缓存的**整份汇总结果**（不重新烧 token）。无缓存/版本不符返回 {}。

    🔴 必须同时满足 `_v == _CACHE_VERSION` **且** `_full is True`：
       `_full` 标记「这份汇总是**通读全部页**得到的」。
       汇总缓存文件名只按 PDF 指纹（路径+mtime+页数），**不含 max_pages**，
       所以调试时用 `max_pages=8` 跑的局部结果会覆盖全量结果 ——
       正式生成时读到它，报告里群众样本就只剩 4 人（实测踩过）。
       旧的无 `_full` 标记的缓存一律视为不可信，自动失效。
    """
    try:
        p = summary_cache_path(pdf_path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if (isinstance(data, dict) and data.get("_v") == _CACHE_VERSION
                    and data.get("_full") is True):
                # 🔴 命中后对年龄分组做**印刷体归一**（「16～35岁」→「16～35」）：
                #    旧缓存写入时还没有归一逻辑，出口统一纠正，
                #    否则「岁」会一直带进表9-1 与第9章正文。
                try:
                    if data.get("age_groups"):
                        data["age_groups"] = canon_public_groups(
                            "age", data.get("age_groups"))
                except Exception:
                    pass
                return data
    except Exception:
        pass
    return {}


def save_summary(pdf_path: str, summary: Dict[str, Any], full: bool = False) -> None:
    """写整份汇总缓存。`full=False`（局部/调试跑）**不落盘** —— 见 load_cached_summary。"""
    if not full:
        logger.debug(f"局部解析结果不入整份汇总缓存: {pdf_path}")
        return
    try:
        summary_cache_path(pdf_path).write_text(
            json.dumps({**summary, "_v": _CACHE_VERSION, "_full": True},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")
    except Exception as e:
        logger.warning(f"问卷汇总结果缓存失败: {e}")


def _norm_option(v: str) -> str:
    """选项文本规范化：去空白、统一波浪号与全半角，避免「56 以上」「56以上」被拆成两个键。"""
    t = re.sub(r'\s+', '', str(v or "")).strip()
    t = t.replace("~", "～").replace("—", "～").replace("-", "～")
    return t


def _tally(d: Dict[str, Any], key: str, value: Any) -> None:
    """把一个选项计入统计（支持字符串与列表）。"""
    if not value:
        return
    vals = value if isinstance(value, list) else [value]
    for v in vals:
        v = _norm_option(v)
        if not v:
            continue
        d.setdefault(key, {})
        d[key][v] = d[key].get(v, 0) + 1


async def _parse_one_page(pdf_path: str, page_index: int, llm, sem: asyncio.Semaphore,
                          dpi: int = 110, attempts: int = 4) -> Dict[str, Any]:
    """识别单页；结果按页缓存。

    🔴 视觉模型**TPM 配额很低**，连续调用必然 429。这里对 429 做**长退避重试**
    （15s/30s/60s），并把最后一页失败降级为「跳过该页」而不是让熔断器连坐。
    """
    cache_file = _cache_dir() / f"{_page_cache_key(pdf_path, page_index)}.pkl"
    if cache_file.exists():
        try:
            return pickle.loads(cache_file.read_bytes())
        except Exception:
            pass

    async with sem:
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(pdf_path)
            pix = doc[page_index].get_pixmap(dpi=dpi)
            png = pix.tobytes("png")
            doc.close()
        except Exception as e:
            logger.warning(f"问卷页渲染失败 p{page_index+1}: {e}")
            return {}

        text = ""
        _b64 = base64.b64encode(png).decode()
        for _att in range(attempts):
            try:
                text = await llm.chat_with_image(
                    _vision_prompt(), _b64,
                    media_type="image/png",
                    system="你是稳定的文书识别助手，只输出严格 JSON。",
                    max_tokens=800,
                )
                break
            except Exception as e:
                _msg = str(e)
                _is_tpm = "429" in _msg or "TPM" in _msg
                _wait = (15, 30, 60, 90)[min(_att, 3)] if _is_tpm else (3, 6, 12, 20)[min(_att, 3)]
                if _att == attempts - 1:
                    logger.warning(f"问卷页识别失败 p{page_index+1}: {_msg[:120]}")
                    return {}
                logger.info(f"问卷页 p{page_index+1} 第{_att+1}次失败（{'限流' if _is_tpm else '其他'}），{_wait}s 后重试")
                await asyncio.sleep(_wait)

    m = re.search(r'\{[\s\S]*\}', str(text or ""))
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {}
    try:
        cache_file.write_bytes(pickle.dumps(data))
    except Exception:
        pass
    return data


async def parse_questionnaire_pdf(
    pdf_path: str,
    llm_service=None,
    max_pages: Optional[int] = None,
    concurrency: int = 1,
    delay_seconds: float = 4.0,
    progress_cb=None,
    use_cache: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """解析座谈会 PDF，返回与人工核验数据集同构的汇总结果。

    整份结果按 PDF 指纹缓存（`summary_<md5>.json`），`use_cache=True` 时命中即秒回，
    不再逐页烧视觉 token。要强制重跑传 `force=True`。

    Returns:
        {
          "total_samples", "valid_samples", "support_count", "oppose_count",
          "neutral_count", "conditional_support", "support_rate", "oppose_rate",
          "identity_groups", "age_groups", "occupation_groups",
          "awareness_levels", "attitude_options", "resolution_methods",
          "dept_survey": {...}, "_source", "_pages", "_dept_pages"
        }
    """
    if use_cache and not force and max_pages is None:
        cached = load_cached_summary(pdf_path)
        if cached:
            logger.info(f"问卷解析命中缓存: {pdf_path}")
            return cached

    if not llm_service:
        from app.services.llm_service import LLMService
        llm_service = LLMService()

    try:
        import fitz
        doc = fitz.open(pdf_path)
        n_pages = len(doc)
        doc.close()
    except Exception as e:
        logger.warning(f"打开问卷 PDF 失败: {e}")
        return {}

    pages = range(min(n_pages, max_pages or n_pages))
    sem = asyncio.Semaphore(max(1, concurrency))

    # 🔴 并发识别（信号量限流）；逐页 await 会让 54 页排成一条长队
    _pg_list = list(pages)
    _done = {"n": 0}

    async def _one(pg):
        r = await _parse_one_page(pdf_path, pg, llm_service, sem)
        _done["n"] += 1
        if progress_cb:
            progress_cb(_done["n"], n_pages)
        if delay_seconds and _done["n"] < len(_pg_list):
            await asyncio.sleep(delay_seconds)   # 🔴 视觉模型 TPM 低，必须节流
        return r

    results = await asyncio.gather(*[_one(pg) for pg in _pg_list])

    # ── 汇总 ──
    pub: Dict[str, Any] = {}
    dept: Dict[str, Any] = {}
    n_pub = n_dept = 0
    support = oppose = neutral = conditional = 0

    # 🔴 手写日期（调查**实际**发生日，报告里用它 —— 不是系统生成时间）
    _pub_dates = _filter_dates([
        _norm_date(r.get("form_date")) for r in results
        if isinstance(r, dict) and str(r.get("form_type") or "").lower() == "public"])
    _dept_dates = _filter_dates([
        _norm_date(r.get("form_date")) for r in results
        if isinstance(r, dict) and str(r.get("form_type") or "").lower() == "dept"])

    for r in results:
        if not isinstance(r, dict):
            continue
        ft = str(r.get("form_type") or "").lower()
        if ft == "public":
            p = r.get("public") or {}
            if not any(p.values()):
                continue
            n_pub += 1
            for k in PUBLIC_QUESTIONS:
                _tally(pub, k, p.get(k))
            att = _norm_option(p.get("attitude"))
            if att == "支持":
                support += 1
            elif att in ("反对", "强烈反对"):
                oppose += 1
            elif att == "有条件支持":
                conditional += 1
            elif att == "无所谓":
                neutral += 1
        elif ft == "dept":
            d = r.get("dept") or {}
            if not any(d.values()):
                continue
            n_dept += 1
            for k in DEPT_QUESTIONS:
                _tally(dept, k, d.get(k))
            # 部门问卷里了解程度也计入 awareness（保持与群众口径一致的可读性）
            # 注意：仅用于部门调查分析，不混入群众统计

    if n_pub == 0 and n_dept == 0:
        return {"_source": "座谈会PDF逐页解析", "_pages": len(results), "_empty": True}

    out: Dict[str, Any] = {
        "_source": "座谈会PDF逐页解析（视觉识别勾选项）",
        "_pages": len(results),
        "_dept_pages": n_dept,
    }
    if n_pub:
        aware = sum(v for k, v in (pub.get("awareness") or {}).items() if k == "了解")
        out.update({
            "total_samples": n_pub,
            "valid_samples": n_pub,
            "support_count": support,
            "oppose_count": oppose,
            "neutral_count": neutral,
            "conditional_support": conditional,
            "support_rate": round(support / n_pub * 100, 1),
            "oppose_rate": round(oppose / n_pub * 100, 1),
            "awareness_rate": round(aware / n_pub * 100, 1),
            "identity_groups": pub.get("identity") or {},
            "age_groups": canon_public_groups("age", pub.get("age") or {}),
            "occupation_groups": pub.get("occupation") or {},
            "awareness_levels": pub.get("awareness") or {},
            "attitude_options": pub.get("attitude") or {},
            "resolution_methods": pub.get("resolution") or {},
        })
        # 🔴 问卷上的**手写日期** → 问卷调查日期（第8/9章写「于X月X日开展问卷调查」用它）。
        #    ⚠️ 用独立键 `questionnaire_date` —— 不能直接占 `survey_date`：
        #       后者的语义是「实地勘察日期」，混用会让正文写成「实地勘察于4月30日」。
        if _pub_dates:
            out["questionnaire_date"] = _pub_dates[0]
            out["questionnaire_date_end"] = _pub_dates[-1]
            out["questionnaire_date_range"] = _cn_range(_pub_dates[0], _pub_dates[-1])
            out["_survey_dates_seen"] = _pub_dates
    if n_dept:
        out["dept_survey"] = {
            "total_units": n_dept,
            "awareness": dept.get("awareness") or {},
            "publicity_satisfaction": dept.get("publicity_satisfaction") or {},
            "policy_understanding": dept.get("policy_understanding") or {},
            "concerns": dept.get("concerns") or {},
            "risk_assessment": dept.get("risk_assessment") or {},
            "stability_confidence": dept.get("stability_confidence") or {},
            "support_attitude": dept.get("support_attitude") or {},
        }
        if _dept_dates:
            out["dept_survey"] = {
                **out["dept_survey"],
                "questionnaire_date": _dept_dates[0],
                "questionnaire_date_range": _cn_range(_dept_dates[0], _dept_dates[-1]),
            }
    try:
        # 🔴 只有「通读全部页」的结果才写入整份汇总缓存；调试用的
        #    `max_pages=N` 局部结果若落盘，会污染正式生成（读到 4 人样本）。
        save_summary(pdf_path, out, full=(max_pages is None))
    except Exception:
        pass
    return out
