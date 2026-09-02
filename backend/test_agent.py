import asyncio
import os
import json
from app.config import settings

# Force litellm to use a mock or standard response if needed
os.environ["OPENAI_API_KEY"] = "mock_key"
os.environ["LITELLM_LOG"] = "DEBUG"

from app.agent.discovery_agent import discovery_agent, BATCH_SIZE

async def main():
    metrics = {
        "entities_discovered": 50,
        "entities_verified": 20,
        "current_domain": "Information Technology",
        "current_subdomain": "SaaS & Cloud",
        "current_keyword": "SaaS startups B2B",
    }

    class MockBatch:
        searches_executed = 0

    prompt = discovery_agent._build_agent_prompt(metrics, MockBatch())
    print("Prompt built:")
    print(prompt)

    print("Invoking LLM...")
    try:
        # We will mock the litellm completion to avoid real API calls since we might not have a valid key
        import litellm
        from unittest.mock import patch
        
        class MockChoice:
            def __init__(self):
                self.message = type("MockMessage", (), {
                    "tool_calls": [
                        type("MockToolCall", (), {
                            "function": type("MockFunction", (), {
                                "name": "search_web",
                                "arguments": json.dumps({"query": "SaaS companies France", "domain": "Information Technology", "subdomain": "SaaS & Cloud", "keyword": "SaaS companies"})
                            })
                        })
                    ]
                })

        class MockResponse:
            choices = [MockChoice()]

        with patch("litellm.completion", return_value=MockResponse()):
            tool_calls = await discovery_agent._invoke_llm_agent(prompt)
            print("Received Tool Calls:")
            print(json.dumps(tool_calls, indent=2))
            assert tool_calls[0]["function"]["name"] == "search_web"
            print("Success! Agent orchestrated search_web.")
            
    except Exception as e:
        print(f"Test error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
