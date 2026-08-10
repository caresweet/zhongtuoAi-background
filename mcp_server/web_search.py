"""MCP Server: Web Search for report data verification.

Provides web search capability to the report generation pipeline:
- Search for latest land acquisition compensation standards
- Verify policy document references
- Look up 区片综合地价 (district comprehensive land prices)
- Retrieve recent stability assessment cases

Uses the same LLM API endpoint with a web-search-capable model, or falls back
to returning structured prompts for Claude Code's built-in WebSearch tool.
"""

import json
import sys
import os

# Ensure backend is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def search_policy(query: str) -> dict:
    """Search for policy/regulation information.

    In production, this would call a real search API.
    For now, returns structured search prompts that Claude Code's
    WebSearch tool can execute.
    """
    return {
        "query": query,
        "source": "web_search_mcp",
        "note": "请使用 WebSearch 工具搜索以下内容，并将结果用于报告编写",
        "search_terms": [
            f"{query} 江苏省 2024 2025 2026",
            f"{query} 淮安市 洪泽区 征地补偿",
            f"{query} 区片综合地价 最新标准",
        ]
    }


def main():
    """MCP server main loop — reads JSON-RPC from stdin, writes to stdout."""
    import asyncio

    async def handle_request(request: dict) -> dict:
        method = request.get("method", "")
        req_id = request.get("id", 0)

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "web-search",
                        "version": "1.0.0"
                    }
                }
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "search_compensation_standard",
                            "description": "搜索最新的征地补偿标准、区片综合地价。用于验证报告中引用的补偿单价是否准确。",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "region": {"type": "string", "description": "搜索区域，如 淮安市洪泽区"},
                                    "year": {"type": "string", "description": "年份，如 2026"}
                                }
                            }
                        },
                        {
                            "name": "search_policy_document",
                            "description": "搜索政策文件全文或关键条款。用于验证报告中的法规引用。",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "policy_name": {"type": "string", "description": "政策文件名或文号，如 苏政发〔2021〕87号"},
                                    "keywords": {"type": "string", "description": "额外关键词"}
                                }
                            }
                        },
                        {
                            "name": "search_similar_cases",
                            "description": "搜索类似征地稳评案例。用于对比和参考。",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "project_type": {"type": "string", "description": "项目类型，如 商业服务业设施用地征收"},
                                    "region": {"type": "string", "description": "区域，如 淮安市"}
                                }
                            }
                        },
                        {
                            "name": "search_news_public_opinion",
                            "description": "搜索相关新闻和舆情。用于了解公众对该类项目的关注点。",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "project_name": {"type": "string", "description": "项目关键词"},
                                    "time_range": {"type": "string", "description": "时间范围，如 近半年"}
                                }
                            }
                        }
                    ]
                }
            }

        elif method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name == "search_compensation_standard":
                region = arguments.get("region", "淮安市")
                year = arguments.get("year", "2026")
                query = f"{region} {year} 征地区片综合地价 征地补偿标准"
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(
                            search_policy(query), ensure_ascii=False, indent=2
                        )}]
                    }
                }

            elif tool_name == "search_policy_document":
                policy = arguments.get("policy_name", "")
                keywords = arguments.get("keywords", "")
                query = f"{policy} {keywords}".strip()
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(
                            search_policy(query), ensure_ascii=False, indent=2
                        )}]
                    }
                }

            elif tool_name == "search_similar_cases":
                project = arguments.get("project_type", "土地征收")
                region = arguments.get("region", "淮安市")
                query = f"{region} {project} 社会稳定风险评估 案例"
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(
                            search_policy(query), ensure_ascii=False, indent=2
                        )}]
                    }
                }

            elif tool_name == "search_news_public_opinion":
                proj = arguments.get("project_name", "")
                time_range = arguments.get("time_range", "近半年")
                query = f"{proj} 征地 舆情 {time_range}"
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(
                            search_policy(query), ensure_ascii=False, indent=2
                        )}]
                    }
                }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"}
        }

    async def run():
        buffer = ""
        for line in sys.stdin:
            buffer += line
            try:
                request = json.loads(buffer)
                buffer = ""
                response = await handle_request(request)
                print(json.dumps(response), flush=True)
            except json.JSONDecodeError:
                continue  # Wait for more input

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
