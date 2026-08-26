"""图片位置绑定 + 附件分类 单元测试（官方规范：10章结构）。"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.image_catalog import (
    _classify_chapter_hint, _classify_appendix, build_image_catalog,
)


def test_folder_hint_maps_to_chapter():
    """正文图片 → 正确章节（官方规范）。"""
    # 位置示意图 → 第1章（决策地理位置）
    assert _classify_chapter_hint('x.jpg', '位置图') == 1
    assert _classify_chapter_hint('x.jpg', '勘测定界') == 1
    # 风险评估流程图 → 第2章
    assert _classify_chapter_hint('x.jpg', '评估流程') == 2
    # 公示照片/座谈会照片 → 第3章
    assert _classify_chapter_hint('x.jpg', '公示照片4') == 3
    assert _classify_chapter_hint('x.jpg', '村民开会现场') == 3
    # 控制性详细规划图 → 第4章
    assert _classify_chapter_hint('x.jpg', '控制性详细规划') == 4


def test_appendix_classification():
    """其余图片 → 附件1-10分类。"""
    assert _classify_appendix('x.jpg', '勘测定界报告') == '附件3 勘测定界报告'
    assert _classify_appendix('x.jpg', '座谈会签到表') == '附件6 座谈会签到表'
    assert _classify_appendix('x.jpg', '稳评问卷调查表') == '附件7 稳评问卷调查表'
    assert _classify_appendix('x.jpg', '专家评审会照片') == '附件8 专家评审会照片'
    assert _classify_appendix('x.jpg', '专家评审意见') == '附件10 专家评审意见'


def test_unknown_goes_to_other():
    """识别不出的 → 其他资料图片。"""
    assert _classify_chapter_hint('x.jpg', '') is None
    assert _classify_appendix('x.jpg', '') == '其他资料图片'


def test_chapter_hint_binds_image():
    """带 folder hint 的图绑定到正确章节。"""
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, '公示照片4'), exist_ok=True)
    p = os.path.join(tmp, '公示照片4', 'a.jpg')
    content = bytes.fromhex('ffd8ffe000104a46494600010100000100010000ffd9')
    open(p, 'wb').write(content)
    catalog = build_image_catalog([p])
    assert 'appendix' in catalog
    ch3 = catalog['by_chapter'].get(3, [])
    assert any(os.path.basename(i['path']) == 'a.jpg' for i in ch3), \
        '公示照片4 应绑定到第3章'
