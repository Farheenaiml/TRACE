import os
import json
import requests
from typing import Optional

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

def call_llm_json(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> Optional[dict]:
    """
    Consolidated helper to call Groq (if key available) or fallback to Ollama,
    expecting JSON responses. Returns parsed dict or None if both fail.
    """
    # Try Groq first
    if GROQ_API_KEY:
        print("LLM call: Attempting Groq...")
        try:
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": temperature
            }
            res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:
            print(f"Groq LLM call failed, attempting Ollama fallback: {e}")

    # Try local Ollama fallback
    try:
        print("LLM call: Attempting Ollama...")
        payload = {
            "model": "llama3.2",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": temperature}
        }
        res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=20)
        res.raise_for_status()
        content = res.json()["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"Ollama LLM call failed or unavailable: {e}")

    return None
