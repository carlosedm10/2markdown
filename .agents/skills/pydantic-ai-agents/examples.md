# Pydantic AI Agents — Examples

## Router agent (structured plan)

**prompts.py:**
```python
ROUTER_SYSTEM_PROMPT = """
You are a query router. Analyze the user's question and decide:
- action: which handler to use (simple_answer, hybrid_search, document_search, etc.)
- confidence: 1-5 how sure you are
- needs_context: whether conversation history is needed

Be concise. Prefer simple_answer when the question is straightforward.
"""
```

**schemas.py:**
```python
from enum import Enum
from pydantic import BaseModel, Field

class Action(str, Enum):
    SIMPLE_ANSWER = "simple_answer"
    SELF_CONTAINED = "self_contained"
    HYBRID_SEARCH = "hybrid_search"
    DOCUMENT_SEARCH = "retrieve_document"
    EXTENSE_SEARCH = "extense_search"


class RouterPlan(BaseModel):
    action: Action = Field(..., description="What action to take")
    confidence: int = Field(..., ge=1, le=5, description="Confidence (1=low, 5=high)")
    needs_context: bool = Field(default=False, description="Include conversation history?")

    class Config:
        use_enum_values = True
```

**agents.py:**
```python
from pydantic_ai import Agent

from .prompts import ROUTER_SYSTEM_PROMPT
from .schemas import RouterPlan

route_agent = Agent(
    model="openai:gpt-4o-mini",
    system_prompt=ROUTER_SYSTEM_PROMPT.strip(),
    output_type=RouterPlan,
)

# Usage
result = route_agent.run_sync("What's the weather in Paris?")
plan = result.output  # RouterPlan(action="simple_answer", confidence=4, needs_context=False)
```

## Simple answer agent (string output)

```python
from pydantic_ai import Agent

from .prompts import ANSWER_SYSTEM_PROMPT

answer_agent = Agent(
    model="anthropic:claude-sonnet-4-5",
    system_prompt=ANSWER_SYSTEM_PROMPT.strip(),
    output_type=str,  # plain text
)
```

## Config-driven model (AI-agnostic)

**config.py:**
```python
import os
from dataclasses import dataclass

@dataclass
class AgentsModelsConfig:
    router_model: str = "openai:gpt-4o-mini"
    answer_model: str = "anthropic:claude-sonnet-4-5"

agents_models_config = AgentsModelsConfig()
# Or from env: os.getenv("ROUTER_MODEL", "openai:gpt-4o-mini")
```

**agents.py:**
```python
from pydantic_ai import Agent

from .config import agents_models_config
from .prompts import ROUTER_SYSTEM_PROMPT, ANSWER_SYSTEM_PROMPT
from .schemas import RouterPlan

route_agent = Agent(
    model=agents_models_config.router_model,
    system_prompt=ROUTER_SYSTEM_PROMPT.strip(),
    output_type=RouterPlan,
)
```

## Enum for discrete choices

When the output must be one of a fixed set of values:

```python
from enum import Enum
from pydantic import BaseModel, Field

class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"

class SentimentResult(BaseModel):
    sentiment: Sentiment = Field(..., description="Detected sentiment")
    score: float = Field(..., ge=0, le=1, description="Confidence score")

    class Config:
        use_enum_values = True
```
