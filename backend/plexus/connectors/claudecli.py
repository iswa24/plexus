"""Run the local `claude` CLI headless (uses the user's Claude Code / Max auth).

This lets the NL2SQL agent use a Claude Max subscription with no API key/credits.
Intended for local testing — productionized apps should use the API or Bedrock.
"""
from __future__ import annotations

import asyncio
import json
import shutil


def available() -> bool:
    return shutil.which("claude") is not None


def cli_model(model_id: str | None) -> str:
    """Map a model id to a CLI alias the subscription understands."""
    m = (model_id or "").lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    return "sonnet"


async def claude_run(prompt: str, system: str | None = None,
                     model: str | None = None, timeout: int = 120) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found on PATH")
    cmd = [exe, "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if system:
        cmd += ["--system-prompt", system]  # replace the default coding prompt
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("claude CLI timed out")
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI error: {(err or b'').decode()[:300]}")
    try:
        data = json.loads(out.decode())
    except json.JSONDecodeError:
        return out.decode().strip()
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI: {str(data.get('result'))[:300]}")
    return (data.get("result") or "").strip()
