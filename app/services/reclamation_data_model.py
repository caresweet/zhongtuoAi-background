# -*- coding: utf-8 -*-
"""复垦数据层 —— 每个数字 = 值 + 来源 + 算法。

把编制人批注里的「思路」机器化：谁都能反查一个数字是从哪张表取的、
还是由哪条式子算出来的。据此可以在生成前拦住两类缺陷：
  - 数字没有出处（编造）
  - 数字与它自己的算法不自洽（样本里真实出现过：批注写 ×15cm、数却是 ×8cm）

⚠️ 只做**纯算术**（四则 + 括号），不做符号运算 —— 不做成通用公式引擎。
"""

import ast
import operator
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 允许出现在算式里的字面量：数字、+-*/ () 空白、以及「中文/字母/下划线」组成的变量名
_SAFE_EXPR = re.compile(r"^[\s\d+\-*/().一-龥A-Za-z_]+$")

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


@dataclass
class TracedValue:
    """一个带出处的数字。"""

    name: str
    value: float
    unit: str = ""
    source: Optional[str] = None      # 取自哪份材料；None 表示非直接取数
    formula: Optional[str] = None     # 算法表达式；None 表示直接取数
    result: float = 0.0               # 按 formula 实算的结果

    def __post_init__(self):
        if self.formula is None:
            self.result = float(self.value)


class ReclamationDataModel:
    """复垦报告的数字账本。"""

    def __init__(self):
        self._values: Dict[str, TracedValue] = {}

    # ── 写入 ────────────────────────────────────────────────

    def set_input(self, name: str, value: float, unit: str = "",
                  source: str = "") -> TracedValue:
        """登记一个直接取自材料的数。"""
        tv = TracedValue(name=name, value=float(value), unit=unit,
                         source=source or None)
        self._values[name] = tv
        return tv

    def derive(self, name: str, formula: str, unit: str = "",
               claimed: Optional[float] = None) -> TracedValue:
        """登记一个派生量。

        `formula` 里可以写别的量名（如「建设用地面积 + 临时用地面积」）。
        `claimed` 是文档中原有的值 —— 给了就在登记时比一次，
        不一致记录在 result 上（由 audit() 报出），**不抛异常**（生成不许被中断）。
        """
        if not _SAFE_EXPR.match(formula):
            raise ValueError(f"算式含不允许的字符：{formula!r}")
        value = self._eval(formula) if claimed is None else float(claimed)
        tv = TracedValue(name=name, value=value, unit=unit, formula=formula)
        tv.result = self._eval(formula)
        self._values[name] = tv
        return tv

    def get(self, name: str) -> TracedValue:
        return self._values[name]

    def names(self) -> List[str]:
        return list(self._values)

    # ── 求值 ────────────────────────────────────────────────

    def _eval(self, formula: str) -> float:
        expr = ast.parse(formula, mode="eval").body

        def _walk(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            if isinstance(node, ast.Name):
                if node.id not in self._values:
                    raise KeyError(f"算式引用了未登记的量：{node.id}")
                return float(self._values[node.id].value)
            if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
                return _OPS[type(node.op)](_walk(node.left), _walk(node.right))
            if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
                return _OPS[type(node.op)](_walk(node.operand))
            raise ValueError(f"算式里有不支持的写法：{formula!r}")

        return _walk(expr)

    # ── 审计 ────────────────────────────────────────────────

    def audit(self, tol: float = 0.01) -> List[str]:
        """返回所有问题描述；空列表＝账本是干净的。"""
        issues: List[str] = []
        for name, tv in self._values.items():
            if not tv.source and not tv.formula:
                issues.append(f"「{name}」既无来源也无算法 —— 数字无出处")
                continue
            if tv.formula and tv.source is None:
                if abs(tv.value - tv.result) > tol:
                    issues.append(
                        f"「{name}」算法与文中值不自洽："
                        f"{tv.formula} = {tv.result:g}，文中却是 {tv.value:g}"
                    )
        return issues
