"""
Generic tool-calling loop against Groq's OpenAI-compatible API. Shared by
both the human chat agent and the AI buyer agent -- they differ only in
system prompt and how step() is driven.

Day 5 addition: step() now records the tool calls and results made during
that turn in self.last_events, so a web frontend can show the same
tool-call transparency the CLI agents have always printed to the console.
"""

import json
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_TURNS = 15
MAX_CONSECUTIVE_TOOL_ERRORS = 3


class Agent:
    def __init__(self, system_prompt: str, tools_schema: list, tool_executor: dict):
        self.tools_schema = tools_schema
        self.tool_executor = tool_executor
        self.messages = [{"role": "system", "content": system_prompt}]
        self.last_events = []

    def _call_llm(self):
        return client.chat.completions.create(
            model=MODEL,
            messages=self.messages,
            tools=self.tools_schema,
            tool_choice="auto",
        )

    def _execute_tool(self, name: str, raw_args: str) -> dict:
        if name not in self.tool_executor:
            return {"error": f"unknown tool '{name}'"}
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError as e:
            return {"error": f"malformed JSON arguments: {e}"}
        try:
            return self.tool_executor[name](**args)
        except TypeError as e:
            return {"error": f"invalid arguments for '{name}': {e}"}

    def step(self, user_message: str | None = None) -> str:
        """Runs one user turn to completion, including any tool calls."""
        self.last_events = []

        if user_message is not None:
            self.messages.append({"role": "user", "content": user_message})

        consecutive_errors = 0

        for _ in range(MAX_TURNS):
            response = self._call_llm()
            msg = response.choices[0].message

            if not msg.tool_calls:
                self.messages.append({"role": "assistant", "content": msg.content or ""})
                return msg.content or ""

            self.messages.append(msg.model_dump(exclude_unset=True))

            for tool_call in msg.tool_calls:
                result = self._execute_tool(tool_call.function.name, tool_call.function.arguments)
                self.last_events.append({
                    "tool": tool_call.function.name,
                    "args": tool_call.function.arguments,
                    "result": result,
                })
                if "error" in result:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                })

            if consecutive_errors >= MAX_CONSECUTIVE_TOOL_ERRORS:
                self.messages.append({
                    "role": "user",
                    "content": "Multiple tool calls failed validation in a row. Stop and summarize the problem instead of retrying further.",
                })

        return "[stopped: max turns reached without a final answer]"