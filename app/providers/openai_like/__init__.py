"""OpenAI-compatible provider helpers."""

from .moderator import choose_next_speaker, generate_synthesis, has_consensus, is_satisfied
from .responses import create_response, stream_response
from .synth import DEFAULT_SYNTH_PROMPT, compose_synthesis, score_candidates

__all__ = [
    "create_response",
    "stream_response",
    "DEFAULT_SYNTH_PROMPT",
    "compose_synthesis",
    "score_candidates",
    "choose_next_speaker",
    "is_satisfied",
    "has_consensus",
    "generate_synthesis",
]
