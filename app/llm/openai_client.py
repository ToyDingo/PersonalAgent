from __future__ import annotations

from typing import Any, Dict

from openai import OpenAI


class OpenAIClient:
    """
    Thin async-friendly wrapper over OpenAI's ChatCompletion API.
    """

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key
        self.client = OpenAI(api_key=api_key)
        self.model = model

    async def simple_ping(self) -> str:
        """
        Minimal call used for health checking the API key.
        """
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "Reply with the word pong.",
                },
            ],
            max_tokens=5,
        )
        return resp.choices[0].message.content

    def chat_with_tools(
        self,
        messages: Any,
        tools: Any,
    ) -> Dict[str, Any]:
        """
        Single-step chat call with tools (function calling).
        """
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        # Convert the pydantic-style response object into a plain dict
        # so existing agent code can treat it like the old client.
        return resp.model_dump()  # type: ignore[no-any-return]

