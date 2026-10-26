"""Runtime configuration. Everything comes from environment variables with sane local defaults."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PROVENANCE_DATA_DIR", ROOT / "data" / "synthetic" / "generated"))
DB_PATH = Path(os.environ.get("PROVENANCE_DB", ROOT / "infra" / "provenance.db"))

# Quality thresholds
PRICE_JUMP_THRESHOLD = float(os.environ.get("PROVENANCE_PRICE_JUMP", "0.25"))  # 25%
SOURCE_DISAGREEMENT_THRESHOLD = float(os.environ.get("PROVENANCE_SOURCE_DISAGREE", "0.01"))  # 1%

# LLM
ANTHROPIC_MODEL = os.environ.get("PROVENANCE_MODEL", "claude-opus-5-5")
LLM_ENABLED = bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("PROVENANCE_LLM", "1") != "0"
LLM_TIMEOUT_S = float(os.environ.get("PROVENANCE_LLM_TIMEOUT", "60"))

# Option pricing default when no implied vol source is available
DEFAULT_VOL = os.environ.get("PROVENANCE_DEFAULT_VOL", "0.30")
