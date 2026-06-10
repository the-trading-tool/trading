"""
gemini_api.py — Backward-compatible wrapper um ai_client.AiClient.

Bestehender Code, der GeminiApi oder GeminiRateLimitError importiert,
funktioniert ohne Änderung weiter.  Neue Funktionalität bitte direkt
über ai_client.AiClient nutzen.
"""
from tradinglib.ai_client import (
    AiClient        as _AiClient,
    AiRateLimitError as GeminiRateLimitError,   # noqa: F401  (re-export)
    AiProviderError,                            # noqa: F401  (re-export)
)
import logging

logger = logging.getLogger(__name__)


class GeminiApi(_AiClient):
    """Alias für AiClient — benutzt Gemini-Provider wenn kein anderer konfiguriert."""

    def __init__(self, question: str = '', username: str = 'admin'):
        """Initialize the AI client and optionally run an immediate question."""
        super().__init__(provider='auto', username=username)
        if question:
            self.run_question(question)

    # _build_asset_prompt ist bereits in AiClient über ai_client._build_asset_prompt
    # eingebunden — kein Override nötig.


if __name__ == '__main__':
    answer = GeminiApi(question="O'Reilly Automotive, Inc.").answer
    print(answer)
