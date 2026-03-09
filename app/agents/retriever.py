"""
app/agents/retriever.py
────────────────────────
Agent 1: The Retriever — Data Gathering Agent

WHAT THIS FILE DOES
────────────────────
- Wakes up when orchestrator begins processing an Active Crisis
- Searches for relevant NDMA SOPs matching the disaster type/region
- Currently reads from local text files in data/sops/
- [FUTURE] Will be upgraded to Azure AI Search hybrid/vector search

HOW IT'S CALLED
────────────────
Called by orchestrator.py as the first step in the AutoGen pipeline:
    sop_text = await retrieve_sops(disaster_type="FIRE", region="Delhi")

AZURE AI SEARCH (Future Upgrade)
──────────────────────────────────
When ready, set these in .env:
    AZURE_SEARCH_ENDPOINT=https://YOUR.search.windows.net
    AZURE_SEARCH_KEY=your_admin_key
    AZURE_SEARCH_INDEX=ndma-sops
The code has a placeholder that will auto-switch to Azure AI Search.
"""
import logging
import os
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.models import DisasterType

logger = logging.getLogger(__name__)
settings = get_settings()

# Local SOP directory (relative to project root)
_SOP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sops"

# Mapping of DisasterType enum → SOP filenames
_SOP_FILE_MAP: dict[str, list[str]] = {
    "FIRE": ["fire_sop.txt"],
    "FLOOD": ["flood_sop.txt"],
    "CYCLONE": ["flood_sop.txt"],         # cyclone uses flood SOP as base
    "EARTHQUAKE": ["earthquake_sop.txt"],
    "GAS_LEAK": ["fire_sop.txt"],         # gas leak follows fire protocol
    "LANDSLIDE": ["earthquake_sop.txt"],  # landslide follows earthquake protocol
    "OTHER": ["fire_sop.txt", "flood_sop.txt", "earthquake_sop.txt"],
}


async def retrieve_sops(
    disaster_type: str | DisasterType,
    region: str | None = None,
    crisis_description: str | None = None,
) -> str:
    """
    Retrieve relevant NDMA Standard Operating Procedures.

    Parameters
    ----------
    disaster_type : str or DisasterType enum
        The type of disaster (FIRE, FLOOD, EARTHQUAKE, etc.)
    region : str, optional
        Geographic region (for future Azure AI Search filtering)
    crisis_description : str, optional
        Free text crisis description (for future semantic search)

    Returns
    -------
    str
        Concatenated SOP text relevant to this disaster type.
        Includes a header indicating the source.
    """
    dtype = disaster_type.value if isinstance(disaster_type, DisasterType) else str(disaster_type).upper()

    # ── Try Azure AI Search first (if configured) ────────────────────────────
    if settings.azure_search_endpoint and settings.azure_search_key:
        azure_result = await _search_azure_ai(dtype, region, crisis_description)
        if azure_result:
            return azure_result
        logger.warning("Azure AI Search returned no results — falling back to local SOPs.")

    # ── Fall back to local SOP files ──────────────────────────────────────────
    return await _search_local_sops(dtype)


async def _search_local_sops(disaster_type: str) -> str:
    """Load SOPs from local text files in data/sops/ directory."""
    sop_files = _SOP_FILE_MAP.get(disaster_type, _SOP_FILE_MAP["OTHER"])
    
    collected_text = []
    for filename in sop_files:
        filepath = _SOP_DIR / filename
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            collected_text.append(f"=== SOURCE: {filename} ===\n{content}")
            logger.info(f"Loaded local SOP: {filename} ({len(content)} chars)")
        else:
            logger.warning(f"SOP file not found: {filepath}")

    if not collected_text:
        return _fallback_sop(disaster_type)

    header = (
        f"[RETRIEVER] Relevant SOPs for {disaster_type} disaster.\n"
        f"Source: Local NDMA SOP files (data/sops/).\n"
        f"Files matched: {len(collected_text)}\n"
        f"{'=' * 60}\n\n"
    )
    return header + "\n\n".join(collected_text)


async def _search_azure_ai(
    disaster_type: str,
    region: str | None,
    description: str | None,
) -> str | None:
    """
    [FUTURE] Search Azure AI Search index for relevant SOPs.
    Placeholder — returns None until azure-search-documents is installed
    and the index is populated.
    """
    try:
        from azure.search.documents import SearchClient
        from azure.core.credentials import AzureKeyCredential

        client = SearchClient(
            endpoint=settings.azure_search_endpoint,
            index_name=settings.azure_search_index,
            credential=AzureKeyCredential(settings.azure_search_key),
        )
        query = f"{disaster_type} {region or ''} {description or ''}"
        results = client.search(
            search_text=query,
            top=5,
            # query_type="semantic", # Ensure semantic search is enabled on your Azure Search resource if using this
        )
        texts = []
        for r in results:
            # Try common Azure AI Search field names
            text = r.get("content") or r.get("merged_content") or r.get("text") or ""
            if text:
                texts.append(text)
        if texts:
            logger.info(f"Azure AI Search returned {len(texts)} chunks from index '{settings.azure_search_index}'")
            return "\n\n".join(texts)

        logger.info("Azure AI Search returned no results.")
        return None

    except Exception as e:
        logger.error(f"Azure AI Search error: {e}")
        return None


def _fallback_sop(disaster_type: str) -> str:
    """Return a minimal fallback SOP when no files are available."""
    return (
        f"[RETRIEVER] No specific SOP found for {disaster_type}.\n"
        f"Applying general NDMA emergency protocol:\n\n"
        f"1. Secure the perimeter and evacuate civilians.\n"
        f"2. Contact local NDRF unit (1078).\n"
        f"3. Set up triage and staging areas.\n"
        f"4. Deploy available responders to affected zones.\n"
        f"5. Update state EOC every 15 minutes.\n"
    )
