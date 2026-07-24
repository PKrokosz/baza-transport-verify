#!/usr/bin/env python3
"""
nvidia_ai.py — NVIDIA Build API email pattern suggestion.
Suggests plausible email patterns when scraping fails.
"""
import os
import json
import asyncio
import aiohttp
from typing import List, Optional

API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
API_KEY = os.getenv("NVIDIA_BUILD_API_KEY", "")

PROMPT_TEMPLATE = (
    "You are an expert at guessing corporate email addresses. "
    "Given the company name '{name}' and its website domain '{domain}', "
    "produce a JSON array of up to 5 plausible email address patterns "
    "using common conventions (firstname.lastname, firstinitiallastname, info@, etc.). "
    "Only return the JSON array, no extra text."
)


async def suggest_emails(company_name: str, domain: Optional[str]) -> List[str]:
    if not API_KEY or not domain:
        return []

    prompt = PROMPT_TEMPLATE.format(name=company_name, domain=domain)
    payload = {
        "model": "nvidia/llama-3.1-nemotron-70b-instruct",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 200,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "[]")
                suggestions = json.loads(content)
                if isinstance(suggestions, list):
                    return [s for s in suggestions if isinstance(s, str) and "@" in s]
    except Exception:
        pass
    return []


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python nvidia_ai.py <company_name> <domain>", file=sys.stderr)
        sys.exit(1)
    results = asyncio.run(suggest_emails(sys.argv[1], sys.argv[2] or None))
    print(json.dumps(results, ensure_ascii=False, indent=2))
