"""The services the agent's nodes use, as one typed object.

Each node receives `deps` and reads `deps.generation_client` and so on. A misspelt name is an
AttributeError at once; the old `kwargs.get("generation_clent")` quietly returned None and the node
carried on without a model.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentDeps:
    db_client: Any = None          # motor database
    generation_client: Any = None  # LLM provider used to generate text
    embedding_client: Any = None   # LLM provider used to embed text
    vectordb_client: Any = None
    template_parser: Any = None
