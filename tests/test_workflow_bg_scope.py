# -*- coding: utf-8 -*-
"""`_run_workflow_bg` 里不能给 `report_service` 做局部绑定。

🔴 线上事故（2026-11-03）：复垦弹窗一暂停就整单失败 ——
   `UnboundLocalError: cannot access local variable 'report_service'`。
根因：函数末尾有一个 `from app.services.report_service import report_service`
局部导入，Python 因此把整个函数里的 `report_service` 都当局部变量；
中断分支先执行的 `report_service._save_sessions()` 就炸了。
本用例钉住：这个函数里只能直接用模块顶部导入的全局对象。
"""

from __future__ import annotations

import inspect
import re


def test_run_workflow_bg_has_no_local_report_service_binding():
    from app.routers import report as R

    src = inspect.getsource(R._run_workflow_bg)
    assert not re.search(
        r"(?m)^\s*from\s+app\.services\.report_service\s+import\s+report_service\b",
        src,
    ), "局部导入会让 report_service 变成局部变量，中断分支的 _save_sessions() 会抛 UnboundLocalError"
    assert not re.search(r"(?m)^\s*report_service\s*=", src), \
        "局部赋值同理，会让 report_service 变成局部变量"
