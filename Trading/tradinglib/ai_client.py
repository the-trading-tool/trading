"""
ai_client.py — Unified AI Client mit Provider-Fallback.

Unterstützte Provider:
  gemini  — Google Gemini API  (google-genai)   → ksp key: 'gapi'
  groq    — Groq Cloud API     (groq package)   → ksp key: 'groq'
  ollama  — Lokale Ollama-Instanz (kein Key)    → ksp key: 'ollama' (url/model)
  auto    — Reihenfolge: Groq → Gemini → Ollama (je nach vorhandenen Keys)

Provider-Auswahl:
  sys_conf key 'ai_provider'  (config.db)
  oder explizit: AiClient(provider='groq')

API-Keys (ksplib/credentials.json):
  'gapi'   → user = Gemini-API-Key
  'groq'   → user = Groq-API-Key
  'ollama' → user = Modellname (z.B. llama3.2), url = Ollama-URL
"""

import logging
import re
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# ── Exceptions ────────────────────────────────────────────────────────────────

class AiRateLimitError(Exception):
    """Alle konfigurierten Provider sind rate-limited oder quota-erschöpft."""

class AiProviderError(Exception):
    """Provider-Konfigurationsfehler oder nicht erreichbar."""

# Alias für Rückwärtskompatibilität mit gemini_api.py
GeminiRateLimitError = AiRateLimitError


# ── Hilfsfunktion ─────────────────────────────────────────────────────────────

def _parse_retry_delay(error_text: str, default: int = 65) -> int:
    """Extrahiert retryDelay-Sekunden aus einem API-Fehlertext."""
    match = re.search(r"retryDelay['\"]:\s*['\"](\d+)s", error_text)
    if match:
        return int(match.group(1))
    match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_text, re.IGNORECASE)
    if match:
        return int(float(match.group(1))) + 1
    return default


# ── Abstract Base ─────────────────────────────────────────────────────────────

class BaseProvider(ABC):
    name: str = ''

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 1024) -> tuple[str, str]:
        """Generiert Text.  Gibt (text, modell_name) zurück."""

    def is_available(self) -> bool:
        """Return True when the provider can accept requests (override for connectivity checks)."""
        return True


# ── Gemini Provider ───────────────────────────────────────────────────────────

_GEMINI_MODELS      = ['gemini-2.0-flash-lite', 'gemini-2.5-flash-lite',
                       'gemini-2.5-flash', 'gemini-2.0-flash']
_GEMINI_MAX_RETRIES = 3
_GEMINI_MAX_WAIT    = 70   # s — länger = Tagesquote, nicht RPM


class GeminiProvider(BaseProvider):
    name = 'gemini'

    def __init__(self, api_key: str):
        """Initialize the Gemini client with the given API key."""
        try:
            from google import genai as _genai
            self.client = _genai.Client(api_key=api_key)
        except ImportError as exc:
            raise AiProviderError(
                "google-genai nicht installiert: pip install google-genai"
            ) from exc

    def generate(self, prompt: str, max_tokens: int = 1024) -> tuple[str, str]:
        """Send prompt to Gemini, cycling through models and retrying on rate-limit errors.

        Returns (text, model_name). Raises AiRateLimitError when all models are exhausted.
        """
        from google.genai import types as _gtypes
        last_exc = None
        for model in _GEMINI_MODELS:
            for attempt in range(1, _GEMINI_MAX_RETRIES + 1):
                try:
                    resp = self.client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=_gtypes.GenerateContentConfig(max_output_tokens=max_tokens),
                    )
                    logger.info("Gemini: OK via %s (attempt %d)", model, attempt)
                    return resp.text, model
                except Exception as exc:
                    err = str(exc)
                    last_exc = exc
                    if '404' in err or 'NOT_FOUND' in err:
                        logger.warning("Gemini: %s not found — skip", model)
                        break
                    if '429' in err or 'RESOURCE_EXHAUSTED' in err:
                        delay = _parse_retry_delay(err)
                        if delay > _GEMINI_MAX_WAIT:
                            logger.warning("Gemini: daily quota on %s — next model", model)
                            break
                        if attempt < _GEMINI_MAX_RETRIES:
                            logger.warning("Gemini: RPM on %s, wait %ds", model, delay)
                            time.sleep(delay)
                            continue
                        break
                    raise AiProviderError(f"Gemini: {exc}") from exc
        raise AiRateLimitError(
            f"Gemini: alle Modelle erschöpft. Letzter Fehler: {last_exc}"
        )


# ── Groq Provider ─────────────────────────────────────────────────────────────

_GROQ_MODELS = [
    'meta-llama/llama-4-scout-17b-16e-instruct',  # 30K TPM · 500K TPD — Primär
    'llama-3.3-70b-versatile',                     # 12K TPM · 100K TPD
    'qwen/qwen3-32b',                              #  6K TPM · 500K TPD · 60 RPM
    'llama-3.1-8b-instant',                        #  6K TPM · 500K TPD — Fallback
]
_GROQ_MAX_RETRIES = 2
_GROQ_MAX_WAIT    = 60


class GroqProvider(BaseProvider):
    name = 'groq'

    def __init__(self, api_key: str):
        """Initialize the Groq client with the given API key."""
        try:
            from groq import Groq
            self.client = Groq(api_key=api_key)
        except ImportError as exc:
            raise AiProviderError(
                "groq nicht installiert: pip install groq"
            ) from exc

    def generate(self, prompt: str, max_tokens: int = 1024) -> tuple[str, str]:
        """Send prompt to Groq, cycling through models and retrying on rate-limit errors.

        Returns (text, model_name). Raises AiRateLimitError when all models are exhausted.
        """
        last_exc = None
        for model in _GROQ_MODELS:
            for attempt in range(1, _GROQ_MAX_RETRIES + 1):
                try:
                    resp = self.client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=max_tokens,
                    )
                    logger.info("Groq: OK via %s (attempt %d)", model, attempt)
                    return resp.choices[0].message.content, model
                except Exception as exc:
                    err = str(exc)
                    last_exc = exc
                    is_rate = ('429' in err or 'rate_limit' in err.lower()
                               or 'rate limit' in err.lower())
                    is_model_gone = ('decommissioned' in err.lower()
                                     or 'deprecated' in err.lower()
                                     or 'not found' in err.lower()
                                     or 'does not exist' in err.lower())
                    if is_rate:
                        delay = _parse_retry_delay(err, default=30)
                        if delay > _GROQ_MAX_WAIT or attempt >= _GROQ_MAX_RETRIES:
                            logger.warning("Groq: quota on %s — next model", model)
                            break
                        logger.warning("Groq: rate limit on %s, wait %ds", model, delay)
                        time.sleep(delay)
                        continue
                    if is_model_gone:
                        logger.warning("Groq: %s unavailable (%s) — next model", model, err[:80])
                        break
                    raise AiProviderError(f"Groq: {exc}") from exc
        raise AiRateLimitError(
            f"Groq: alle Modelle erschöpft. Letzter Fehler: {last_exc}"
        )


# ── GitHub Models Provider ────────────────────────────────────────────────────
# Endpoint: https://models.inference.ai.azure.com  (OpenAI-kompatibel)
# Token:    GitHub PAT (Settings → Developer settings → Personal access tokens → kein Scope nötig)
# KSP-Key:  'github'  (user = PAT-Token)
# Limits:   ~15 Req/min · ~150 Req/Tag · max 4K Output-Tokens pro Request

_GITHUB_MODELS      = ['gpt-4o-mini', 'gpt-4o']
_GITHUB_ENDPOINT    = 'https://models.inference.ai.azure.com'
_GITHUB_MAX_RETRIES = 2
_GITHUB_MAX_WAIT    = 60


class GitHubModelsProvider(BaseProvider):
    name = 'github'

    def __init__(self, api_key: str):
        """Initialize with a GitHub Personal Access Token."""
        try:
            from openai import OpenAI as _OpenAI
            self.client = _OpenAI(base_url=_GITHUB_ENDPOINT, api_key=api_key)
        except ImportError as exc:
            raise AiProviderError(
                "openai-Paket nicht installiert: "
                ".venv/Scripts/python.exe -m pip install openai"
            ) from exc

    def generate(self, prompt: str, max_tokens: int = 1024) -> tuple[str, str]:
        """Send prompt to GitHub Models (GPT-4o-mini → GPT-4o fallback)."""
        # GitHub Models: max 4096 Output-Tokens
        effective_max = min(max_tokens, 4096)
        last_exc = None
        for model in _GITHUB_MODELS:
            for attempt in range(1, _GITHUB_MAX_RETRIES + 1):
                try:
                    resp = self.client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=effective_max,
                    )
                    logger.info("GitHub Models: OK via %s (attempt %d)", model, attempt)
                    return resp.choices[0].message.content, model
                except Exception as exc:
                    err      = str(exc)
                    last_exc = exc
                    is_rate  = '429' in err or 'rate' in err.lower()
                    is_gone  = ('decommissioned' in err.lower() or 'not found' in err.lower()
                                or 'does not exist' in err.lower())
                    if is_rate:
                        delay = _parse_retry_delay(err, default=30)
                        if delay > _GITHUB_MAX_WAIT or attempt >= _GITHUB_MAX_RETRIES:
                            logger.warning("GitHub Models: quota on %s — next model", model)
                            break
                        logger.warning("GitHub Models: rate limit on %s, wait %ds", model, delay)
                        time.sleep(delay)
                        continue
                    if is_gone:
                        logger.warning("GitHub Models: %s unavailable — next model", model)
                        break
                    raise AiProviderError(f"GitHub Models: {exc}") from exc
        raise AiRateLimitError(
            f"GitHub Models: alle Modelle erschöpft. Letzter Fehler: {last_exc}"
        )


# ── Ollama Provider ───────────────────────────────────────────────────────────

_OLLAMA_DEFAULT_URL   = 'http://localhost:11434'
_OLLAMA_DEFAULT_MODEL = 'llama3.2'
_OLLAMA_TIMEOUT       = 120


class OllamaProvider(BaseProvider):
    name = 'ollama'

    def __init__(self, url: str = _OLLAMA_DEFAULT_URL, model: str = _OLLAMA_DEFAULT_MODEL):
        """Configure the Ollama provider with the server URL and model name."""
        self.url   = url.rstrip('/')
        self.model = model

    def generate(self, prompt: str, max_tokens: int = 1024) -> tuple[str, str]:
        """Send prompt to the local Ollama instance and return (text, model_name).

        Raises AiProviderError when the Ollama server is not reachable.
        """
        import requests
        try:
            resp = requests.post(
                f"{self.url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False,
                      "options": {"num_predict": max_tokens}},
                timeout=_OLLAMA_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json().get('response', ''), self.model
        except Exception as exc:
            raise AiProviderError(
                f"Ollama nicht erreichbar ({self.url}). "
                f"Bitte 'ollama serve' starten.\nDetail: {exc}"
            ) from exc

    def is_available(self) -> bool:
        """Return True when the Ollama server responds within 2 seconds."""
        import requests
        try:
            requests.get(f"{self.url}/api/tags", timeout=2)
            return True
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return the list of model names available on the local Ollama instance."""
        import requests
        try:
            resp = requests.get(f"{self.url}/api/tags", timeout=5)
            return [m['name'] for m in resp.json().get('models', [])]
        except Exception:
            return []


# ── AiClient (einheitlicher Einstiegspunkt) ───────────────────────────────────

class AiClient:
    """Unified AI Client mit Provider-Fallback zur Laufzeit.

    Verwendung:
        client = AiClient()                        # auto aus config.db
        client = AiClient(provider='groq')         # explizit
        text   = client.analyze_asset(ticker, ctx)
        text   = client.run_question("...")

    Im 'auto'-Modus werden alle verfügbaren Provider (Groq → Gemini → Ollama)
    als Liste gespeichert.  run_question() versucht jeden der Reihe nach —
    erst wenn alle mit AiRateLimitError scheitern, wird der Fehler propagiert.
    """

    def __init__(self, provider: str = 'auto', username: str = 'admin'):
        """Resolve and initialize the AI provider list.

        provider: 'auto' reads from config.db key 'ai_provider' and builds a
        list of all configured providers (Groq → Gemini → Ollama). Pass
        'groq', 'gemini', or 'ollama' to force a specific single provider.
        """
        self.answer        = ''
        self.model_used    = ''
        self.provider_name = ''
        self.provider_log: list[dict] = []   # [{provider, model, status, error}]
        self._providers    = _resolve_provider_list(provider, username)
        self.provider_name = self._providers[0].name if self._providers else ''

    def run_question(self, question: str, max_tokens: int = 1024) -> str:
        """Send a question to the first available provider, falling back on AiRateLimitError.

        Populates self.provider_log with one entry per attempted provider:
          {'provider': str, 'model': str|None, 'status': 'ok'|'failed', 'error': str|None}
        """
        self.provider_log = []
        errors: list[str] = []
        for prov in self._providers:
            try:
                text, model        = prov.generate(question, max_tokens=max_tokens)
                self.answer        = text
                self.model_used    = model
                self.provider_name = prov.name
                self.provider_log.append({
                    'provider': prov.name, 'model': model,
                    'status': 'ok', 'error': None,
                })
                return text
            except AiRateLimitError as exc:
                err_short = str(exc)[:120]
                logger.warning("AiClient: %s erschöpft — versuche nächsten Provider", prov.name)
                errors.append(f"{prov.name}: {exc}")
                self.provider_log.append({
                    'provider': prov.name, 'model': None,
                    'status': 'failed', 'error': err_short,
                })
            # AiProviderError (Konfig-Fehler) wird direkt propagiert — kein Fallback
        raise AiRateLimitError(
            "Alle Provider erschöpft.\n\n" + "\n".join(errors)
        )

    def analyze_asset(self, ticker: str, context: dict) -> str:
        """Prompt aus lokalem Kontext-Dict bauen und KI-Analyse zurückgeben."""
        prompt = _build_asset_prompt(ticker, context)
        return self.run_question(prompt)


# ── Provider-Auflösung ────────────────────────────────────────────────────────

def _resolve_provider(provider: str, username: str = 'admin') -> BaseProvider:
    """Instanziiert den gewünschten Provider; 'auto' probiert Reihenfolge."""
    from tradinglib import ksplib
    from tradinglib import system_config as sysconf

    if provider == 'auto':
        cfg      = sysconf.SystemConfig(username=username)
        provider = cfg.get_value('ai_provider', 'auto')

    ksp = ksplib.Ksp()

    def _get_key(name: str) -> str:
        creds = ksp.get_ksp(name)
        return (creds.get('user', '') if isinstance(creds, dict) else '') or ''

    def _get_url(name: str, default: str) -> str:
        creds = ksp.get_ksp(name)
        return (creds.get('url', '') if isinstance(creds, dict) else '') or default

    if provider == 'groq':
        key = _get_key('groq')
        if not key:
            raise AiProviderError(
                "Groq API-Key fehlt. Bitte unter 'groq' in den API Credentials eintragen."
            )
        return GroqProvider(api_key=key)

    if provider == 'ollama':
        url   = _get_url('ollama', _OLLAMA_DEFAULT_URL)
        model = _get_key('ollama') or _OLLAMA_DEFAULT_MODEL
        return OllamaProvider(url=url, model=model)

    if provider == 'gemini':
        key = _get_key('gapi')
        if not key:
            raise AiProviderError(
                "Gemini API-Key fehlt. Bitte unter 'gapi' in den API Credentials eintragen."
            )
        return GeminiProvider(api_key=key)

    # ── auto: Groq → Gemini → Ollama ─────────────────────────────────────────
    errors = []

    groq_key = _get_key('groq')
    if groq_key:
        try:
            return GroqProvider(api_key=groq_key)
        except AiProviderError as e:
            errors.append(f"Groq: {e}")

    gemini_key = _get_key('gapi')
    if gemini_key:
        try:
            return GeminiProvider(api_key=gemini_key)
        except AiProviderError as e:
            errors.append(f"Gemini: {e}")

    ollama_url   = _get_url('ollama', _OLLAMA_DEFAULT_URL)
    ollama_model = _get_key('ollama') or _OLLAMA_DEFAULT_MODEL
    prov = OllamaProvider(url=ollama_url, model=ollama_model)
    if prov.is_available():
        return prov
    errors.append(f"Ollama: nicht erreichbar unter {ollama_url}")

    raise AiProviderError(
        "Kein AI-Provider verfügbar.\n\n"
        "Optionen:\n"
        "  • Groq (kostenlos):  https://console.groq.com → Key unter 'groq' speichern\n"
        "  • Gemini (kostenlos):https://aistudio.google.com → Key unter 'gapi' speichern\n"
        "  • Ollama (lokal):    https://ollama.com → 'ollama serve' starten\n\n"
        + "\n".join(errors)
    )


def _resolve_provider_list(provider: str, username: str = 'admin') -> list[BaseProvider]:
    """Gibt eine geordnete Liste aller verfügbaren Provider zurück.

    Im 'auto'-Modus enthält die Liste alle konfigurierten Provider
    (Groq → GitHub Models → Gemini → Ollama), sodass AiClient.run_question()
    sie der Reihe nach versuchen kann.
    Für explizite Provider ('groq', 'github', 'gemini', 'ollama') wird
    eine einelementige Liste zurückgegeben.
    """
    from tradinglib import ksplib
    from tradinglib import system_config as sysconf

    if provider == 'auto':
        cfg      = sysconf.SystemConfig(username=username)
        provider = cfg.get_value('ai_provider', 'auto')

    ksp = ksplib.Ksp()

    def _get_key(name: str) -> str:
        creds = ksp.get_ksp(name)
        return (creds.get('user', '') if isinstance(creds, dict) else '') or ''

    def _get_url(name: str, default: str) -> str:
        creds = ksp.get_ksp(name)
        return (creds.get('url', '') if isinstance(creds, dict) else '') or default

    # ── Explizite Einzel-Provider ─────────────────────────────────────────────
    if provider == 'groq':
        key = _get_key('groq')
        if not key:
            raise AiProviderError(
                "Groq API-Key fehlt. Bitte unter 'groq' in den API Credentials eintragen."
            )
        return [GroqProvider(api_key=key)]

    if provider == 'gemini':
        key = _get_key('gapi')
        if not key:
            raise AiProviderError(
                "Gemini API-Key fehlt. Bitte unter 'gapi' in den API Credentials eintragen."
            )
        return [GeminiProvider(api_key=key)]

    if provider == 'github':
        key = _get_key('github')
        if not key:
            raise AiProviderError(
                "GitHub PAT fehlt. Bitte unter 'github' in den API Credentials eintragen.\n"
                "Token erstellen: GitHub → Settings → Developer settings → "
                "Personal access tokens → Generate new token (kein Scope nötig)."
            )
        return [GitHubModelsProvider(api_key=key)]

    if provider == 'ollama':
        url   = _get_url('ollama', _OLLAMA_DEFAULT_URL)
        model = _get_key('ollama') or _OLLAMA_DEFAULT_MODEL
        return [OllamaProvider(url=url, model=model)]

    # ── auto: Provider in konfigurierter Reihenfolge sammeln ────────────────
    # Standard: groq → github → gemini → ollama
    # Überschreibbar via config.db-Key 'ai_provider_order' (Liste von Namen)
    _DEFAULT_ORDER = ['groq', 'github', 'gemini', 'ollama']
    cfg_order = sysconf.SystemConfig(username=username).get_value(
        'ai_provider_order', _DEFAULT_ORDER
    )
    if not isinstance(cfg_order, list) or not cfg_order:
        cfg_order = _DEFAULT_ORDER
    # Sicherstellen dass alle bekannten Provider berücksichtigt werden
    # (nicht in der Liste = ans Ende)
    for p in _DEFAULT_ORDER:
        if p not in cfg_order:
            cfg_order.append(p)

    def _build_provider(name: str) -> BaseProvider | None:
        """Instanziiert einen Provider anhand seines Namens; None wenn Key fehlt/unavailable."""
        try:
            if name == 'groq':
                key = _get_key('groq')
                return GroqProvider(api_key=key) if key else None
            if name == 'github':
                key = _get_key('github')
                return GitHubModelsProvider(api_key=key) if key else None
            if name == 'gemini':
                key = _get_key('gapi')
                return GeminiProvider(api_key=key) if key else None
            if name == 'ollama':
                url   = _get_url('ollama', _OLLAMA_DEFAULT_URL)
                model = _get_key('ollama') or _OLLAMA_DEFAULT_MODEL
                prov  = OllamaProvider(url=url, model=model)
                return prov if prov.is_available() else None
        except AiProviderError as exc:
            logger.warning("%s nicht verfügbar: %s", name, exc)
        return None

    providers: list[BaseProvider] = []
    for pname in cfg_order:
        prov = _build_provider(pname)
        if prov is not None:
            providers.append(prov)

    if not providers:
        raise AiProviderError(
            "Kein AI-Provider verfügbar.\n\n"
            "Optionen:\n"
            "  • Groq (kostenlos):          https://console.groq.com\n"
            "  • GitHub Models (kostenlos): github.com Settings → PAT → Key unter 'github'\n"
            "  • Gemini (kostenlos):        https://aistudio.google.com\n"
            "  • Ollama (lokal):            https://ollama.com\n"
        )

    return providers


# ── Prompt-Builder (shared, kein Provider-State nötig) ───────────────────────

def _build_asset_prompt(ticker: str, ctx: dict) -> str:
    """Build the structured German-language analysis prompt from the asset context dict.

    ctx keys used: recent_ohlc, sim_grouped, indicator_values, strategy_name,
    stockIndex, buy_date, buy_price, buy_query, sell_query, longName, sector, industry.
    """
    ohlc_lines = ctx.get('recent_ohlc', '')
    if hasattr(ohlc_lines, 'to_string'):
        ohlc_lines = ohlc_lines.to_string(index=False)

    sim_section = ''
    for group, values in ctx.get('sim_grouped', {}).items():
        kv = '  |  '.join(f'{k}: {v}' for k, v in values.items())
        sim_section += f'{group}:\n  {kv}\n'
    if not sim_section:
        ind = ctx.get('indicator_values', '')
        sim_section = ind.to_string(index=False) if hasattr(ind, 'to_string') else str(ind)

    strategy_block = (
        f"Strategie: {ctx.get('strategy_name', 'n/a')}  |  "
        f"Index: {ctx.get('stockIndex', 'n/a')}  |  "
        f"Kaufdatum: {ctx.get('buy_date', 'n/a')}  |  "
        f"Kaufkurs: {ctx.get('buy_price', 'n/a')}\n"
        f"Buy-Bedingung:  {ctx.get('buy_query', 'n/a')}\n"
        f"Sell-Bedingung: {ctx.get('sell_query', 'n/a')}"
    )

    return (
        "Du bist ein erfahrener Aktienanalyst mit Zugang zu allgemeinem Finanzwissen "
        "sowie den folgenden lokalen Marktdaten.\n\n"
        f"=== ASSET ===\n"
        f"{ctx.get('longName', ticker)} ({ticker})\n"
        f"Sektor: {ctx.get('sector', 'unbekannt')}  |  "
        f"Branche: {ctx.get('industry', 'unbekannt')}\n\n"
        f"=== STRATEGIE ===\n"
        f"{strategy_block}\n\n"
        f"=== SIMULATIONSKENNZAHLEN (lokale Daten) ===\n"
        f"{sim_section}\n"
        f"=== KURSVERLAUF (letzte 20 Handelstage, lokal) ===\n"
        f"{ohlc_lines}\n\n"
        "Erstelle eine strukturierte Analyse auf Deutsch mit genau diesen vier Abschnitten:\n\n"
        "**Unternehmen:** 2–3 Sätze Kurzbeschreibung des Unternehmens – "
        "was es macht, Hauptprodukte/Dienstleistungen, Marktstellung "
        "(basierend auf deinem allgemeinen Finanzwissen).\n\n"
        "**Wettbewerb:** 2–3 Sätze zur aktuellen Wettbewerbssituation – "
        "wichtigste Konkurrenten, Marktanteile, strategische Stärken/Schwächen "
        "(basierend auf deinem allgemeinen Finanzwissen).\n\n"
        "**Technisch:** 1–2 Sätze technische Begründung warum das Asset gerade "
        "interessant ist (konkrete Indikatorwerte aus den lokalen Daten nennen).\n\n"
        "**Fazit:** 1 Satz Handlungsempfehlung inkl. Hauptrisiko (Stop-Loss, "
        "Markov-Regime oder Volatilität aus den lokalen Daten).\n"
    )
