"""End-to-end test: create session → fill data → upload images → generate → validate.

Tests the full report generation pipeline including:
- Template selection + session creation
- Section-by-section Q&A collecting
- Image upload from network URLs
- Report assembly
- Format auto-fix
- Spec compliance check
"""

import asyncio
import json
import time
import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx


BASE_URL = "http://localhost:8000/api/v1"


async def test_full_pipeline():
    """Full end-to-end generation test."""
    print("=" * 60)
    print("  端到端测试：智能报告生成 + 格式校验")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=120.0) as client:
        # ═══════════════════════════════════════════════════════════════
        # Step 1: Start generation session
        # ═══════════════════════════════════════════════════════════════
        print("\n[1/5] 创建生成会话...")
        resp = await client.post(
            f"{BASE_URL}/reports/generate/start",
            json={
                "initial_message": "生成一份社会稳定风险评估报告",
                "template_id": 6,  # 金湖社会稳定报告模板
            },
        )
        data = resp.json()
        assert resp.status_code == 200, f"Failed: {data}"
        session_id = data["data"]["session_id"]
        print(f"  ✅ 会话创建成功: {session_id[:12]}...")

        # ═══════════════════════════════════════════════════════════════
        # Step 2: Send report title (triggers setup → collecting)
        # ═══════════════════════════════════════════════════════════════
        print("\n[2/5] 发送报告标题...")
        title = (
            "金征预告〔2026〕3号（高铁枢纽北片区开发地块项目）"
            "土地征收决策社会稳定风险评估报告"
        )

        # We need to stream the response for the chat endpoint
        collecting_messages = []
        async with client.stream(
            "POST",
            f"{BASE_URL}/reports/generate/{session_id}/chat",
            json={"message": title},
        ) as response:
            assert response.status_code == 200
            current_event = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    current_event = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        event_data = json.loads(line[6:])
                        event_data["_event"] = current_event
                        if current_event == "collecting_question":
                            collecting_messages.append(event_data)
                    except json.JSONDecodeError:
                        pass

        print(f"  ✅ 标题已发送，进入收集阶段，收到 {len(collecting_messages)} 个问题")

        # ═══════════════════════════════════════════════════════════════
        # Step 3: Answer collecting questions (simulated)
        # ═══════════════════════════════════════════════════════════════
        print("\n[3/5] 模拟用户填写数据...")

        # Prepare answers for key questions
        answers = {
            "决策名称": f"{title}决策",
            "决策单位": "金湖县戴楼街道办事处",
            "拟征地位置": "拟征收土地位于戴楼街道戴楼社区四组、七组范围内",
            "征收范围": "拟征收土地面积约150亩，其中农用地约120亩，建设用地约30亩",
            "资金筹措": "项目资金由县财政统筹安排，已列入2026年度财政预算",
            "实施周期": "2026年7月至2026年12月，共计6个月",
        }

        answered = 0
        for answer_hint, answer_text in answers.items():
            async with client.stream(
                "POST",
                f"{BASE_URL}/reports/generate/{session_id}/chat",
                json={"message": answer_text},
            ) as response:
                assert response.status_code == 200
                current_event = ""
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        current_event = line[7:].strip()
                    elif line.startswith("data: "):
                        try:
                            event_data = json.loads(line[6:])
                            if current_event == "placeholder_filled":
                                answered += 1
                        except json.JSONDecodeError:
                            pass
            await asyncio.sleep(0.2)  # Avoid overwhelming the server

        print(f"  ✅ 已填写 {answered} 个占位符")

        # ═══════════════════════════════════════════════════════════════
        # Step 4: Upload a network image (for location map)
        # ═══════════════════════════════════════════════════════════════
        print("\n[4/5] 下载并上传网络图片...")

        # Use a stable placeholder map image
        image_url = "https://picsum.photos/seed/map/800/600"

        try:
            img_resp = await client.get(image_url)
            if img_resp.status_code == 200:
                # Upload to backend
                files = {
                    "file": (
                        "location_map.png",
                        img_resp.content,
                        "image/png",
                    )
                }
                upload_resp = await client.post(
                    f"{BASE_URL}/reports/generate/{session_id}/upload",
                    files=files,
                )
                if upload_resp.status_code == 200:
                    upload_data = upload_resp.json()
                    file_path = upload_data.get("data", {}).get("file_path", "")
                    print(f"  ✅ 图片上传成功: {file_path}")

                    # Send with image attachment
                    async with client.stream(
                        "POST",
                        f"{BASE_URL}/reports/generate/{session_id}/chat",
                        json={
                            "message": "位于戴楼街道戴楼社区四组、七组范围内，地理位置如图所示",
                            "attachments": [file_path],
                        },
                    ) as response:
                        assert response.status_code == 200
                        async for _ in response.aiter_lines():
                            pass
                    print("  ✅ 图片描述已发送")
                else:
                    print("  ⚠️ 图片上传失败，继续测试")
            else:
                print("  ⚠️ 网络图片下载失败，跳过图片测试")
        except Exception as e:
            print(f"  ⚠️ 图片上传异常: {e}，继续测试")

        # ═══════════════════════════════════════════════════════════════
        # Step 5: Generate the report
        # ═══════════════════════════════════════════════════════════════
        print("\n[5/5] 生成报告并校验...")

        gen_events = []
        report_id = None
        download_url = None

        async with client.stream(
            "POST",
            f"{BASE_URL}/reports/generate/{session_id}/chat",
            json={"message": "生成报告"},
        ) as response:
            assert response.status_code == 200
            current_event = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    current_event = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        event_data = json.loads(line[6:])
                        event_data["_event"] = current_event
                        gen_events.append(event_data)

                        # Track key events
                        if current_event == "complete":
                            report_id = event_data.get("report_id")
                            download_url = event_data.get("download_url")

                        # Print thinking/fix/check progress
                        if "content" in event_data:
                            content = str(event_data["content"])
                            if any(kw in content for kw in [
                                "修复", "校验", "合规", "✅", "⚠️", "生成报告",
                            ]):
                                print(f"  {content[:120]}")
                    except json.JSONDecodeError:
                        pass

        if report_id:
            print(f"\n  ✅ 报告生成成功!")
            print(f"     Report ID: {report_id}")
            print(f"     Download: {download_url}")

            # Also run the spec checker directly on the generated file
            print(f"\n  📐 运行格式规范校验...")
            from app.validation.diff_engine import ReportDiffEngine

            # Find the generated file
            generated_dir = Path("storage/generated")
            gen_file = generated_dir / f"{session_id}.docx"
            if gen_file.exists():
                engine = ReportDiffEngine()

                # Compare with the best example
                example = generated_dir / "b5a55e9774a64a82853c2bef0e6846e4.docx"
                diff = await engine.compare(
                    str(gen_file),
                    str(example) if example.exists() else "",
                )
                print(f"     {diff.to_markdown()[:500]}...")
            else:
                print(f"     ⚠️ 生成文件未找到: {gen_file}")
        else:
            print("\n  ❌ 报告生成失败!")
            print(f"     收到 {len(gen_events)} 个事件")
            for evt in gen_events[-5:]:
                print(f"     {json.dumps(evt, ensure_ascii=False)[:200]}")

        # ═══════════════════════════════════════════════════════════════
        # Summary
        # ═══════════════════════════════════════════════════════════════
        print("\n" + "=" * 60)
        if report_id:
            print("  ✅ 端到端测试通过！")
            print(f"  Report ID: {report_id}")
            print(f"  总事件数: {len(gen_events)}")
        else:
            print("  ❌ 端到端测试失败")
        print("=" * 60)

        return {
            "session_id": session_id,
            "report_id": report_id,
            "download_url": download_url,
            "event_count": len(gen_events),
        }


if __name__ == "__main__":
    result = asyncio.run(test_full_pipeline())
    sys.exit(0 if result["report_id"] else 1)
