"""
Central config. Reads from environment / .env — never hardcode secrets here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Hindsight
HINDSIGHT_BASE_URL = os.getenv(
    "HINDSIGHT_BASE_URL",
    "http://localhost:8888"
)

BANK_ID = os.getenv(
    "HINDSIGHT_BANK_ID",
    "cognitive-debt-auditor-demo"
)

# Local LLM via Ollama
OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://localhost:11434/v1"
)

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "llama3.2:latest"
)

LLM_AVAILABLE = True

# Kept for compatibility with existing code
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Dataset
DATASET_PATH = os.getenv(
    "DATASET_PATH",
    "dataset.json"
)

SEED_STATE_PATH = os.getenv(
    "SEED_STATE_PATH",
    ".seed_state.json"
)

DEFAULT_DEMO_PROPOSAL = (
    "Remove the legacy authentication adapter and replace it with the new OAuth flow."
)


# Memory tag taxonomy
class MemoryTag:
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"