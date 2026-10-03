import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

load_dotenv()


class BashSession:
    """A persistent, interactive Bash session."""

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        """Spawn the interactive bash subprocess if not already running."""
        if self.process is None:
            self.process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-i",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

    async def run(self, command: str) -> str:
        """Send a command to the persistent shell and read until the completion marker."""
        await self.start()
        assert self.process is not None
        assert self.process.stdin is not None
        assert self.process.stdout is not None

        # Unique marker lets us know when the command has finished.
        marker = "__PYDANTIC_AI_DONE_7f3a9c__"
        payload = f"{command}\nprintf '\\n{marker}\\n'\n"

        self.process.stdin.write(payload.encode())
        await self.process.stdin.drain()

        output = await self.process.stdout.readuntil(f"\n{marker}\n".encode())
        return output.decode(errors="replace").rstrip()

    async def close(self) -> None:
        """Terminate the persistent shell session cleanly."""
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=0.2)
            except (TimeoutError, ProcessLookupError):
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
                await self.process.wait()
            self.process = None


@asynccontextmanager
async def bash_session() -> AsyncIterator[BashSession]:
    """Provide a managed BashSession instance as an async context manager."""
    session = BashSession()
    try:
        yield session
    finally:
        await session.close()


def create_bash_agent(
    model: str | FunctionModel | None = None,
) -> Agent[BashSession, str]:
    """Create a pydantic-ai Agent configured with a persistent BashSession dependency."""
    if model is None:
        model = os.getenv("LLM_MODEL", "anthropic:claude-haiku-4-5")

    agent: Agent[BashSession, str] = Agent(
        model,
        deps_type=BashSession,
    )

    @agent.tool
    async def bash_exec(ctx: RunContext[BashSession], command: str) -> str:
        """Execute a command in the persistent Bash session."""
        return await ctx.deps.run(command)

    return agent


async def test_bash_session_persistence(tmp_path):
    """Verify that BashSession maintains working directory and environment across runs."""
    async with bash_session() as bash:
        test_dir = tmp_path / "repl_demo"
        await bash.run(f"mkdir -p '{test_dir}' && cd '{test_dir}'")
        pwd_out = await bash.run("pwd")
        assert str(test_dir) in pwd_out

        await bash.run("SESSION_VAR=pydantic_ai_test")
        echo_out = await bash.run("echo $SESSION_VAR")
        assert "pydantic_ai_test" in echo_out


async def test_pydantic_ai_agent_with_bash_tool(tmp_path):
    """Verify pydantic-ai Agent runs bash_exec tool calls and receives execution output."""
    test_dir = tmp_path / "agent_demo"
    recorded_returns: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn = sum(isinstance(m, ModelResponse) for m in messages)
        if turn == 0:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "bash_exec",
                        {
                            "command": f"mkdir -p '{test_dir}' && cd '{test_dir}' && echo 'hello pydantic-ai' > hello.txt && cat hello.txt"
                        },
                    )
                ]
            )
        tool_return = next(
            p for p in messages[-1].parts if isinstance(p, ToolReturnPart)
        )
        recorded_returns.append(str(tool_return.content))
        return ModelResponse(parts=[TextPart(content="file created and verified")])

    agent = create_bash_agent(FunctionModel(script))

    async with bash_session() as bash:
        result = await agent.run(
            f"Create {test_dir}, cd into it, create hello.txt, then show me its contents.",
            deps=bash,
        )

    assert result.output == "file created and verified"
    assert (test_dir / "hello.txt").read_text().strip() == "hello pydantic-ai"
    assert "hello pydantic-ai" in recorded_returns[0]


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY is not set for live model testing",
)
async def test_pydantic_ai_agent_live_model(tmp_path):
    """Live end-to-end test with Claude using bash_exec tool in persistent REPL."""
    test_dir = tmp_path / "live_demo"
    agent = create_bash_agent("anthropic:claude-haiku-4-5")

    async with bash_session() as bash:
        result = await agent.run(
            f"Create {test_dir}, cd into it, create hello.txt with content 'hackathon-bash-verified', then show me its contents.",
            deps=bash,
        )

    assert (test_dir / "hello.txt").exists()
    assert (test_dir / "hello.txt").read_text().strip() == "hackathon-bash-verified"
    assert len(result.output) > 0


async def main() -> None:
    """Run interactive demo with live model."""
    target_dir = "/tmp/demo_pydantic_ai"
    async with bash_session() as bash:
        agent = create_bash_agent()
        result = await agent.run(
            f"Create {target_dir}, cd into it, create hello.txt, then show me its contents.",
            deps=bash,
        )
        print("Agent output:")
        print(result.output)


if __name__ == "__main__":
    asyncio.run(main())
