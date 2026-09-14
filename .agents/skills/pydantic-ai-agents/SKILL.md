---
name: pydantic-ai-agents
description: Creates AI-agnostic agents using pydantic_ai with structured output. Use when building agents with pydantic_ai, defining router/planner agents, or when the user mentions pydantic_ai, Agent, OpenAIModel, AnthropicModel, or structured LLM output.
---

# Pydantic AI Agents

Create AI-agnostic agents using pydantic_ai. Agents are portable across providers (OpenAI, Anthropic, etc.) by swapping the model; prompts and output schemas stay the same.

## Quick start

1. Define output schema (Pydantic model or simple type).
2. Add system prompt to `prompts.py` as a constant.
3. Instantiate `Agent` with `model`, `system_prompt`, and `output_type`.

## Agent template

**Always use the string model format** — no imports needed, fully config-driven:

```python
from pydantic_ai import Agent

route_agent = Agent(
    model="openai:gpt-4o-mini",
    system_prompt=ROUTER_SYSTEM_PROMPT.strip(),
    output_type=RouterPlan,
)
```

String format: `"provider:model"` or `"gateway/provider:model"`. Pydantic AI selects the model class, provider, and profile automatically. Load from config/env to switch models without code changes.

## Prompts

Store all prompts in `prompts.py` as constants:

```python
# prompts.py

ROUTER_SYSTEM_PROMPT = """
You are a query router. Analyze the user's question and decide the best action.
...
"""
```

Use `.strip()` when passing to the agent to avoid leading/trailing whitespace.

## Structured output (exact format)

When the agent must return a strict schema, use a Pydantic model with `Field` descriptions and enums:

```python
from enum import Enum
from pydantic import BaseModel, Field

class Action(str, Enum):
    """Router actions - what should we do with this query?"""
    SIMPLE_ANSWER = "simple_answer"
    SELF_CONTAINED = "self_contained"
    HYBRID_SEARCH = "hybrid_search"
    DOCUMENT_SEARCH = "retrieve_document"
    EXTENSE_SEARCH = "extense_search"


class RouterPlan(BaseModel):
    """Router output: decides how to handle the user's query."""
    action: Action = Field(..., description="What action to take")
    confidence: int = Field(
        ..., ge=1, le=5,
        description="Confidence in routing decision (1=low, 5=high)"
    )
    needs_context: bool = Field(
        default=False,
        description="Whether to include conversation history in the prompt",
    )

    class Config:
        use_enum_values = True
```

- Use `Field(..., description="...")` so the LLM understands each field.
- Use `str` enums for actions/choices; `use_enum_values = True` serializes as string values.
- Add `ge`/`le` for numeric constraints.

## Model strings (AI-agnostic)

Use string format — swap providers by changing the string:

```python
# Direct to provider
model="openai:gpt-4o-mini"
model="anthropic:claude-sonnet-4-5"

# Via Pydantic AI Gateway (single key, cost limits, failover)
model="gateway/openai:gpt-5.2"
model="gateway/anthropic:claude-sonnet-4-6"

# Config-driven
model=os.getenv("ROUTER_MODEL", "openai:gpt-4o-mini")
model=agents_models_config.router_model
```

Override per run: `agent.run_sync("...", model="anthropic:claude-sonnet-4-5")`.

Only use explicit model classes (`OpenAIChatModel`, etc.) when you need a custom provider, `base_url`, or API key override.

## Running agents

```python
# Sync
result = route_agent.run_sync("What is the capital of France?")
plan: RouterPlan = result.output

# Async
result = await route_agent.run("What is the capital of France?")
plan: RouterPlan = result.output
```

## File layout

```
<module>/
├── prompts.py      # All system prompts as constants
├── schemas.py      # Pydantic output models (RouterPlan, etc.)
├── agents.py       # Agent instances
└── config.py       # Model names / settings (optional)
```

## Additional resources

- For more examples (router, planner, simple answer), see [examples.md](examples.md).
