"""
Customer Support AI Agent — Complete Implementation
====================================================
All TODOs implemented.

Run locally:
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore configure --entrypoint main.py --name ai-support-agent
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands.tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")


# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
app = BedrockAgentCoreApp()


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Replace these placeholders with your actual AWS resource values.
GATEWAY_URL = os.getenv(
    "GATEWAY_URL",
    "https://<your-gateway-url>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
)
KB_ID     = os.getenv("KB_ID", "<your-kb-id>")
REGION    = os.getenv("REGION", "us-east-1")
MEMORY_ID = os.getenv("MEMORY_ID", "<your-memory-id>")


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
model_id = "global.amazon.nova-2-lite-v1:0"

model = BedrockModel(model_id=model_id, region_name=REGION)
memory_client = MemoryClient(region_name=REGION)
_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    try:
        strategies = mem_client.get_memory_strategies(memory_id=memory_id)
        namespaces = {}
        for strategy in strategies:
            strategy_type = strategy.get("type") or strategy.get("strategyType")
            ns_list = strategy.get("namespaces", [])
            if strategy_type and ns_list:
                namespaces[strategy_type] = ns_list[0]
        return namespaces
    except Exception as e:
        logger.warning(f"Could not fetch memory strategies: {e}")
        return {}


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.namespaces = get_namespaces(memory_client, memory_id)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        try:
            messages = event.agent.messages
            if not messages:
                return

            last_message = messages[-1]
            if last_message.get("role") != "user":
                return

            content = last_message.get("content", [])
            if not content or not isinstance(content[0], dict):
                return

            # Skip tool results
            if "toolResult" in content[0] or "toolUse" in content[0]:
                return

            user_query = content[0].get("text", "")
            if not user_query:
                return

            memory_lines = []
            for strategy_type, namespace_template in self.namespaces.items():
                try:
                    resolved_ns = namespace_template.format(actorId=self.actor_id)
                    memories = self.memory_client.retrieve_memories(
                        memory_id=self.memory_id,
                        namespace=resolved_ns,
                        query=user_query,
                        top_k=5,
                    )
                    for mem in memories or []:
                        text = mem.get("content", {}).get("text", "").strip()
                        if text:
                            memory_lines.append(f"[{strategy_type}] {text}")
                except Exception as e:
                    logger.warning(f"Memory retrieval failed: {e}")

            if memory_lines:
                prefix = "Customer Context:\n" + "\n".join(memory_lines)
                event.agent.messages[-1]["content"][0]["text"] = (
                    f"{prefix}\n\n{user_query}"
                )
        except Exception as e:
            logger.warning(f"retrieve_customer_context error: {e}")

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        try:
            messages = event.agent.messages
            customer_query = None
            agent_response = None

            for msg in reversed(messages):
                role = msg.get("role")
                content = msg.get("content", [])
                if not content or not isinstance(content[0], dict):
                    continue
                text = content[0].get("text", "").strip()
                if not text:
                    continue
                if role == "assistant" and agent_response is None:
                    agent_response = text
                elif role == "user" and customer_query is None:
                    if "toolResult" not in content[0] and "toolUse" not in content[0]:
                        customer_query = text
                        break

            if customer_query and agent_response:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=self.session_id,
                    messages=[
                        (customer_query, "USER"),
                        (agent_response, "ASSISTANT"),
                    ],
                )
                logger.info(f"Saved interaction for actor={self.actor_id}")
        except Exception as e:
            logger.warning(f"save_support_interaction error: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    if not KB_ID or KB_ID.startswith("<"):
        return "Knowledge base not configured."

    try:
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": 5}
            },
        )
        results = resp.get("retrievalResults", [])
        if not results:
            return f"No relevant information found for: {query}"

        chunks = []
        for r in results:
            text = r.get("content", {}).get("text", "").strip()
            if text:
                chunks.append(text)
        return "\n---\n".join(chunks)
    except Exception as e:
        logger.error(f"KB search failed: {e}")
        return f"Knowledge base search error: {e}"


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    code = f'''
import json

earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}

loyalty_points = {int(loyalty_points)}
tier = {json.dumps(str(tier))}
order_total = {float(order_total)}
product_category = {json.dumps(str(product_category))}

tier_rate = tier_rates.get(tier, 0.00)
earn_rate = earn_rates.get(product_category, 1)

# Redeem points: floor to nearest 500, cap at 50% of order
max_redeemable_dollars = order_total * 0.5
points_redeemed = min((loyalty_points // 500) * 500, int(max_redeemable_dollars * 100))
points_value = points_redeemed / 100.0

subtotal_after_points = order_total - points_value
tier_discount = round(subtotal_after_points * tier_rate, 2)
final_total = round(subtotal_after_points - tier_discount, 2)
total_savings = round(order_total - final_total, 2)
points_earned = int(final_total * earn_rate)
remaining_points = loyalty_points - points_redeemed

result = {{
    "tier": tier,
    "tier_discount_rate": tier_rate,
    "order_total": order_total,
    "points_redeemed": points_redeemed,
    "points_value_usd": points_value,
    "subtotal_after_points": subtotal_after_points,
    "tier_discount_usd": tier_discount,
    "final_total": final_total,
    "total_savings": total_savings,
    "points_earned": points_earned,
    "remaining_points": remaining_points,
}}
print(json.dumps(result))
'''

    try:
        with code_session(REGION) as code_client:
            response = code_client.invoke(
                "executeCode",
                {
                    "code": code,
                    "language": "python",
                    "clearContext": True,
                },
            )
            for event in response.get("stream", []):
                result = event.get("result", {})
                if "content" in result:
                    for c in result["content"]:
                        text = c.get("text", "")
                        if text:
                            return text
                return json.dumps(result)

        return json.dumps({"error": "no result from code interpreter"})

    except Exception as e:
        logger.warning(f"Code interpreter failed: {e}. Using fallback.")

        tier_rates = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
        tier_rate = tier_rates.get(tier, 0.00)
        tier_discount = round(order_total * tier_rate, 2)
        final_total = round(order_total - tier_discount, 2)
        return json.dumps({
            "tier": tier,
            "tier_discount_rate": tier_rate,
            "order_total": order_total,
            "tier_discount_usd": tier_discount,
            "final_total": final_total,
            "total_savings": tier_discount,
            "points_redeemed": 0,
            "remaining_points": loyalty_points,
            "note": "fallback: code interpreter unavailable, points not redeemed",
        })


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a helpful and knowledgeable customer support assistant for an e-commerce store.

Your capabilities:
1. Order tracking and shipping status — use the order_tracker tools
2. Refund and return processing — use the refund_processor tool
3. Product information, return policies, warranty, loyalty tiers — use search_knowledge_base
4. Remember customer details across sessions — handled automatically
5. Loyalty discount calculations — ALWAYS use calculate_loyalty_discount (never calculate yourself)
6. Live web browsing — use the browser tool

Guidelines:
- Be friendly, concise, and professional
- ALWAYS use search_knowledge_base before answering questions about policies or products
- ALWAYS use calculate_loyalty_discount for any numeric discount calculation
- Never guess numbers
- If you cannot help, offer to escalate to a human
"""


@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    try:
        user_input = payload.get("prompt") or payload.get("message") or "Hello!"
        actor_id = payload.get("customer_id") or "anonymous"
        session_id = payload.get("session_id") or str(uuid.uuid4())

        logger.info(f"Invoke: actor={actor_id} session={session_id}")

        # Memory hook
        memory_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID,
        )

        # Browser tool
        agent_core_browser = AgentCoreBrowser(region=REGION, session_timeout=600)

        # Base tools
        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            agent_core_browser.browser,
        ]

        # Gateway tools via MCP
        try:
            mcp_client = MCPClient(
                lambda: streamable_http_client(url=GATEWAY_URL)
            )
            with mcp_client:
                gateway_tools = mcp_client.list_tools_sync()
                logger.info(f"Loaded {len(gateway_tools)} gateway tools")
                tools.extend(gateway_tools)

                agent = Agent(
                    model=model,
                    system_prompt=SYSTEM_PROMPT,
                    tools=tools,
                    hooks=[memory_hook],
                    state={"actor_id": actor_id, "session_id": session_id},
                )
                response = agent(user_input)
        except Exception as e:
            logger.warning(f"Gateway connection failed: {e}. Running without gateway tools.")
            agent = Agent(
                model=model,
                system_prompt=SYSTEM_PROMPT,
                tools=tools,
                hooks=[memory_hook],
                state={"actor_id": actor_id, "session_id": session_id},
            )
            response = agent(user_input)

        # Extract text from response
        if hasattr(response, "message"):
            msg = response.message
            content = msg.get("content", []) if isinstance(msg, dict) else []
            if content and isinstance(content[0], dict):
                return content[0].get("text", str(response))

        return str(response)

    except Exception as e:
        logger.error(f"Invoke failed: {e}")
        return {"error": str(e), "message": "I'm sorry, something went wrong. Please try again."}


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()