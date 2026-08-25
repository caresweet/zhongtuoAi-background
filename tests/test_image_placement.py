"""图片位置绑定 单元测试（治「图片放错」）。"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.image_catalog import _classify_chapter_hint, build_image_catalog


def test_folder_hint_maps_to_chapter():
    """文件夹名 → 确定性章节绑定。"""
    assert _classify_chapter_hint('x.jpg', '专家评审会照片') == 5
    assert _classify_chapter_hint('x.jpg', '临时用地现场照片') == 2
    assert _classify_chapter_hint('x.jpg', '公示照片4') == 3
    assert _classify_chapter_hint('x.jpg', '村民开会现场') == 3
    assert _classify_chapter_hint('x.jpg', '群众座谈会扫描') == 3
    assert _classify_chapter_hint('x.jpg', '稳评专家意见扫描') == 8


def test_folder_hint_unknown_returns_none():
    """无提示文件夹 → None（走类别竞争兜底）。"""
    assert _classify_chapter_hint('x.jpg', '') is None
    assert _classify_chapter_hint('x.jpg', '其他杂项') is None


def test_chapter_hint_binds_image():
    """带 folder hint 的图绑定到正确章节，不参与竞争。"""
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, '公示照片4'), exist_ok=True)
    p = os.path.join(tmp, '公示照片4', 'a.jpg')
    content = bytes.fromhex('ffd8ffe000104a46494600010100000100010000ffd9')
    open(p, 'wb').write(content)
    catalog = build_image_catalog([p])
    ch3 = catalog['by_chapter'].get(3, [])
    assert any(os.path.basename(i['path']) == 'a.jpg' for i in ch3), \
        '公示照片4 应绑定到第3章'
