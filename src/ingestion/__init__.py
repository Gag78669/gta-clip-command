"""Video intake, metadata extraction, audio demuxing and creator rights authorization gate."""

from src.ingestion.rights_gate import RightsGate, RightsCheckResult, UnauthorizedCreatorError

__all__ = [
    "RightsGate",
    "RightsCheckResult",
    "UnauthorizedCreatorError",
]
