# -*- coding: utf-8 -*-
"""上传文件类型白名单的测试。

业主 2026-10-30 线上实测：整文件夹上传时，Shapefile 的 8 个配套文件
（.shp/.shx/.dbf/.prj/.sbn/.sbx/.shp.xml/.cpg）全部被 400 挡掉，
而同批的 dwg/pdf/xlsx 都成功。这里钉住「整组放行 + 分类正确」。
"""
import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from app.config import settings
from app.services.file_service import FileService


def _upload(name: str, content: bytes = b"x") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=name,
                      headers=Headers({"content-type": "application/octet-stream"}))


def test_shapefile_components_are_allowed():
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".qpj", ".xml"):
        assert ext in settings.ALLOWED_ATTACHMENT_EXTENSIONS, f"{ext} 必须放行"


def test_cad_still_allowed():
    assert ".dwg" in settings.ALLOWED_ATTACHMENT_EXTENSIONS
    assert ".dxf" in settings.ALLOWED_ATTACHMENT_EXTENSIONS


@pytest.mark.asyncio
async def test_shapefile_is_classified_as_vector(monkeypatch):
    # 不落盘：直接命中「同内容已存在」，只为验证类型判定
    monkeypatch.setattr(
        FileService, "_find_existing_by_hash",
        classmethod(lambda cls, h: "images/井神红线0907.shp"),
    )
    r = await FileService.save_attachment(_upload("井神红线0907.shp"))
    assert r["file_type"] == "vector"
    assert r["relative_path"].endswith(".shp")


@pytest.mark.asyncio
async def test_shp_xml_is_accepted(monkeypatch):
    monkeypatch.setattr(
        FileService, "_find_existing_by_hash",
        classmethod(lambda cls, h: "images/井神红线0907.shp.xml"),
    )
    r = await FileService.save_attachment(_upload("井神红线0907.shp.xml"))
    assert r["file_type"] == "vector"


@pytest.mark.asyncio
async def test_unknown_type_still_rejected():
    with pytest.raises(HTTPException) as ei:
        await FileService.save_attachment(_upload("图纸.bak"))
    assert ei.value.status_code == 400
    assert "不支持的文件类型" in ei.value.detail
