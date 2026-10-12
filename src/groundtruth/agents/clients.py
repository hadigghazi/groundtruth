"""Model clients for the agents, chosen by environment variables.

GROUNDTRUTH_LLM=foundry   Microsoft Foundry (FOUNDRY_PROJECT_ENDPOINT; managed identity or Azure CLI login)
GROUNDTRUTH_LLM=openai    any OpenAI-compatible endpoint (OPENAI_API_KEY, optional OPENAI_BASE_URL)
GROUNDTRUTH_LLM=scripted  the deterministic scripted client (default; no network)

GROUNDTRUTH_MODEL_LARGE runs the Tactician; GROUNDTRUTH_MODEL_SMALL runs the Verifier's judge and the
Persona writers, which do simpler work and need to be fast. GROUNDTRUTH_REASONING_EFFORT (low, medium,
high) caps the Tactician's thinking when the large model is a reasoning model such as gpt-5-mini.
"""

from __future__ import annotations

import os


def make_clients(kind: str | None = None):
    """Returns (large, small) chat clients."""
    kind = (kind or os.environ.get("GROUNDTRUTH_LLM", "scripted")).lower()
    large = os.environ.get("GROUNDTRUTH_MODEL_LARGE", "")
    small = os.environ.get("GROUNDTRUTH_MODEL_SMALL", large)
    if kind == "foundry":
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

        endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
        cred = DefaultAzureCredential()
        return (FoundryChatClient(project_endpoint=endpoint, model=large, credential=cred),
                FoundryChatClient(project_endpoint=endpoint, model=small, credential=cred))
    if kind == "openai":
        from agent_framework.openai import OpenAIChatCompletionClient

        base = os.environ.get("OPENAI_BASE_URL") or None
        key = os.environ["OPENAI_API_KEY"]
        return (OpenAIChatCompletionClient(model=large, api_key=key, base_url=base),
                OpenAIChatCompletionClient(model=small, api_key=key, base_url=base))
    from .fake import ScriptedChatClient

    bad = os.environ.get("GROUNDTRUTH_SCRIPTED_BAD_FIRST_DRAFT") == "1"
    return ScriptedChatClient(bad_first_draft=bad), ScriptedChatClient()
