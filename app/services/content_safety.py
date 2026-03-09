"""
app/services/content_safety.py
────────────────────────────────
Azure AI Content Safety — Text Analysis

WHERE TO PLUG IN YOUR AZURE KEYS
─────────────────────────────────
Open your .env file and fill in these 2 values:

  AZURE_CONTENT_SAFETY_ENDPOINT=https://YOUR_RESOURCE.cognitiveservices.azure.com/
  AZURE_CONTENT_SAFETY_KEY=<paste Key 1 from Azure Portal>

HOW TO GET THE KEY
──────────────────
1. Go to portal.azure.com
2. Search for "Content Safety" and open your resource
   (or create one: Create Resource → AI + Machine Learning → Content Safety)
3. Click "Keys and Endpoint" in the left menu
4. Copy "Key 1" → paste into AZURE_CONTENT_SAFETY_KEY
5. Copy the Endpoint URL → paste into AZURE_CONTENT_SAFETY_ENDPOINT

WHAT THIS FILE DOES
───────────────────
- Called once per SMS (on the translated English text) in ingest.py
- Detects: Hate speech, Violence, Self-harm, Sexual content
- Also detects likely test/spam messages via a keyword heuristic
- Any category with severity ≥ 2 sets is_spam=True on the CrisisReport
- Flagged reports are excluded from the PostGIS clustering count
- Graceful fallback: if key is empty, all reports pass through (is_spam=False)
"""
import logging
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Keywords that strongly indicate test/spam messages (not real emergencies)
_SPAM_KEYWORDS = {
    "test", "testing", "hello", "hi", "demo", "sample",
    "fake", "prank", "joke", "abcd", "xyz", "asdf",
}

# Azure Content Safety severity threshold (0-6 scale, 0=safe, 6=extreme)
# We flag anything ≥ 2 (Low severity and above)
_SEVERITY_THRESHOLD = 2


async def is_spam_or_unsafe(text: str) -> bool:
    """
    Returns True if the text should be flagged as spam or unsafe.

    Checks (in order):
    1. Keyword heuristic: very short messages or known test phrases
    2. Azure AI Content Safety: Hate/Violence/SelfHarm/Sexual categories

    Parameters
    ----------
    text : str
        The translated (English) SMS body text.

    Returns
    -------
    bool
        True  → mark report as spam, exclude from clustering
        False → legitimate crisis report, proceed with pipeline
    """
    # ── Quick heuristic check (no API needed) ────────────────────────────────
    cleaned = text.strip().lower()

    # Very short messages are likely test messages
    if len(cleaned) < 5:
        logger.info(f"Spam heuristic: message too short '{cleaned}'")
        return True

    # Known test/spam keywords
    if cleaned in _SPAM_KEYWORDS or cleaned.split()[0] in _SPAM_KEYWORDS:
        logger.info(f"Spam heuristic: keyword match '{cleaned[:30]}'")
        return True

    # ── Azure Content Safety check ────────────────────────────────────────────
    if not settings.azure_content_safety_key:
        logger.warning(
            "AZURE_CONTENT_SAFETY_KEY not set — skipping safety check. "
            "Set it in .env to enable abuse filtering."
        )
        return False

    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory
        from azure.core.credentials import AzureKeyCredential
        from azure.core.exceptions import HttpResponseError

        client = ContentSafetyClient(
            endpoint=settings.azure_content_safety_endpoint,
            credential=AzureKeyCredential(settings.azure_content_safety_key),
        )

        request = AnalyzeTextOptions(
            text=text[:1000],  # API limit: 1000 chars
            categories=[
                TextCategory.HATE,
                TextCategory.VIOLENCE,
                TextCategory.SELF_HARM,
                TextCategory.SEXUAL,
            ],
            output_type="FourSeverityLevels",
        )

        response = client.analyze_text(request)

        for category_result in response.categories_analysis:
            if category_result.severity >= _SEVERITY_THRESHOLD:
                logger.warning(
                    f"Content Safety flagged: {category_result.category} "
                    f"severity={category_result.severity} | '{text[:60]}'"
                )
                return True

        logger.debug(f"Content Safety: clean — '{text[:60]}'")
        return False

    except Exception as e:
        logger.error(f"Azure Content Safety error: {e} — allowing report through")
        return False  # Fail open: don't block real emergencies on API errors
