"""QuantBot 意图识别 — 使用 LLM 判断用户意图

intent:
  - chat              一般对话（走 SSE 流式）
  - factor_evolution  启动 RD-Agent 因子演化（AlphaAgent）
  - factor_inquiry    查询/回测/解读/导出已挖因子
"""

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

INTENT_SYSTEM_PROMPT = """\
你是一个量化投资助手的意图识别引擎。分析用户的输入，判断其意图类型。

返回 JSON 格式（不要输出其他任何内容）：
{
  "intent": "chat" | "factor_evolution" | "factor_inquiry",
  "factor_type": "价值|动量|波动|质量|成长|技术|综合" (仅 factor_evolution 时需要),
  "description": "用户需求描述/挖掘方向" (仅 factor_evolution 时需要),
  "constraints": {"universe": "csi300", "loop_n": 3, ...} (可选参数),
  "action": "list" | "backtest" | "explain" | "export" | "report" (仅 factor_inquiry 时需要),
  "factor_id": "因子 id" (可选，factor_inquiry 指定因子时),
  "target": "因子名或描述" (可选)
}

【factor_evolution】触发表达：
- "帮我挖掘XXX因子" / "evolve factors for XXX" / "生成一批价值因子"
- "挖连板/筹码/隔夜/资金流方向的因子" / "找一些低波动高收益的因子"
constraints.universe 从表述中抽取：
- 含 沪深300/csi300 → csi300；含 中证500/csi500 → csi500；含 中证1000/csi1000 → csi1000
- 含 创业板/gem → gem；含 科创板/star → star；含 全A/全部A股/all_a → all_a
- 未提及则不填（默认 csi300）
constraints.loop_n：含 轮数/5轮/10轮 数字时抽取（默认 3）。

【factor_inquiry】触发表达（对已挖因子做后续操作）：
- 列表/报告: "看下因子结果" / "挖的因子怎么样" / "因子排行榜"
- 回测:     "回测一下这个因子" / "因子回测跑一下"
- 解读:     "解释一下这个因子" / "这个因子逻辑是什么"
- 导出:     "提交这个因子" / "导出高分因子入库"
action 映射：含"回测"→backtest；含"解释/解读/逻辑"→explain；含"导出/提交/入库"→export；
其他（结果/排行榜/怎么样/列表/看一下）→report。
factor_id / target 在用户指名因子时填写。

其他所有情况返回 intent: "chat"。\
"""


def _get_llm_config() -> tuple[str, str, str]:
    """获取 LLM 配置"""
    base_url = (
        os.getenv("AI_IDE_LLM_BASE_URL")
        or os.getenv("AI_IDE_BASE_URL")
        or "https://api.deepseek.com"
    )
    model = os.getenv("AI_IDE_LLM_MODEL") or os.getenv("AI_IDE_MODEL") or "deepseek-v4-pro"
    api_key = (
        os.getenv("AI_IDE_LLM_API_KEY")
        or os.getenv("AI_IDE_API_KEY")
        or os.getenv("OPENAI_API_KEY", "")
    )
    return base_url, model, api_key


async def parse_intent(message: str, history: list[dict] | None = None) -> dict:
    """调用 LLM 解析用户意图"""
    base_url, model, api_key = _get_llm_config()

    if not api_key or "mock-api-key" in api_key:
        # 未配置 API key 时走规则匹配 fallback
        return _rule_based_intent(message)

    messages = [{"role": "system", "content": INTENT_SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-6:])  # 只取最近 6 条历史消息
    messages.append({"role": "user", "content": message})

    try:
        from backend.services.engine.alpha_agent.llm_client import (
            env_extra_headers,
            openai_chat_url,
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                openai_chat_url(base_url),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    **env_extra_headers(),
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": 512,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            content = body["choices"][0]["message"]["content"].strip()

            # 提取 JSON（兼容 ```json ... ``` 包裹）
            if content.startswith("```"):
                content = content.split("```", 2)[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            parsed = json.loads(content)
            if parsed.get("intent") not in ("chat", "factor_evolution", "factor_inquiry"):
                return _rule_based_intent(message)
            return parsed
    except Exception as e:
        logger.warning(f"Intent LLM call failed, falling back to rule-based: {e}")
        return _rule_based_intent(message)


def _rule_based_intent(message: str) -> dict:
    """基于关键词的意图识别 fallback"""
    lower = message.lower()

    # 因子后续操作（优先判断：含"因子"+"回测/解释/导出/结果"等）
    inquiry_keywords = {
        "回测": "backtest",
        "解释": "explain",
        "解读": "explain",
        "逻辑": "explain",
        "导出": "export",
        "提交": "export",
        "入库": "export",
    }
    if "因子" in lower or "factor" in lower:
        for kw, action in inquiry_keywords.items():
            if kw in lower:
                return {
                    "intent": "factor_inquiry",
                    "action": action,
                    "target": "",
                    "factor_id": "",
                    "constraints": {},
                }
        if any(k in lower for k in ("结果", "排行榜", "怎么样", "列表", "看一下", "查看")):
            return {
                "intent": "factor_inquiry",
                "action": "report",
                "target": "",
                "factor_id": "",
                "constraints": {},
            }

    evolution_keywords = [
        "挖掘", "evolve", "进化", "生成因子", "factor evolution",
        "因子进化", "找一些", "生成一批", "挖掘因子",
    ]
    for kw in evolution_keywords:
        if kw in lower:
            return {
                "intent": "factor_evolution",
                "factor_type": "综合",
                "description": message,
                "constraints": {},
            }
    return {"intent": "chat"}
