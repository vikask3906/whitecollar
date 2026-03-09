"""
app/services/translator.py
───────────────────────────
Azure AI Translator — Text Translation REST API v3.0

WHERE TO PLUG IN YOUR AZURE KEYS
─────────────────────────────────
Open your .env file and fill in these 3 values:

  AZURE_TRANSLATOR_KEY=<paste Key 1 from Azure Portal>
  AZURE_TRANSLATOR_REGION=<your resource region, e.g. eastus>
  AZURE_TRANSLATOR_ENDPOINT=https://api.cognitive.microsofttranslator.com

HOW TO GET THE KEY
──────────────────
1. Go to portal.azure.com
2. Open your Translator resource
3. Click "Keys and Endpoint" in the left menu
4. Copy "Key 1" → paste into AZURE_TRANSLATOR_KEY
5. Copy the Region (e.g. "eastasia") → paste into AZURE_TRANSLATOR_REGION
6. The endpoint is always: https://api.cognitive.microsofttranslator.com

WHAT THIS FILE DOES
───────────────────
- Called once per inbound SMS in ingest.py
- Auto-detects regional language (Hindi, Tamil, Marathi, Bengali, Telugu, etc.)
- Translates to English for downstream Content Safety + Clustering
- Graceful fallback: if key is empty, returns original text unchanged
"""
import logging
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Azure Translator REST API version
_API_VERSION = "3.0"
_TRANSLATE_PATH = "/translate"


async def translate_to_english(text: str) -> tuple[str, str]:
    """
    Translate ``text`` to English using Azure AI Translator.

    Returns
    -------
    (translated_text, detected_language_code)

    Examples
    --------
    - ("यहाँ आग लग गई है", "hi")   → ("There is a fire here", "hi")
    - ("Fire near my house", "en") → ("Fire near my house", "en")

    Fallback
    --------
    If AZURE_TRANSLATOR_KEY is not set, or if an error occurs,
    returns the original text with language code "en" so the pipeline
    continues uninterrupted.
    """
    # ── Graceful fallback: no key configured ─────────────────────────────────
    if not settings.azure_translator_key:
        logger.warning(
            "AZURE_TRANSLATOR_KEY not set — skipping translation. "
            "Set it in .env to enable multi-language support."
        )
        return text, "en"

    url = f"{settings.azure_translator_endpoint.rstrip('/')}{_TRANSLATE_PATH}"
    params = {
        "api-version": _API_VERSION,
        "to": "en",          # target language
        # "from" is omitted → auto-detect source language
    }
    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_translator_key,
        "Ocp-Apim-Subscription-Region": settings.azure_translator_region,
        "Content-Type": "application/json",
    }
    body = [{"text": text}]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, params=params, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()

        # Response structure:
        # [{"detectedLanguage": {"language": "hi", "score": 1.0},
        #   "translations": [{"text": "...", "to": "en"}]}]
        result = data[0]
        translated_text: str = result["translations"][0]["text"]
        detected_lang: str = result.get("detectedLanguage", {}).get("language", "en")

        logger.info(
            f"Translated '{text[:50]}' | detected={detected_lang} → en"
        )
        return translated_text, detected_lang

    except httpx.HTTPStatusError as e:
        logger.error(
            f"Azure Translator HTTP error {e.response.status_code}: {e.response.text}"
        )
    except Exception as e:
        logger.error(f"Azure Translator unexpected error: {e}")

    # Fallback on any error
    return text, "en"
