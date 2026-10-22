import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def ask_llm(prompt, model="meterless/auto"):
    """Send a prompt to the Nightshift OpenAI-compatible API and return the reply.

    Prints the model that actually answered.
    """
    base_url = os.getenv("NIGHTSHIFT_URL")
    api_key = os.getenv("METERLESS_RUNTIME_TOKEN")

    if not base_url:
        raise RuntimeError("NIGHTSHIFT_URL is not set. Copy .env.example to .env and fill it in.")
    if not api_key:
        raise RuntimeError(
            "METERLESS_RUNTIME_TOKEN is not set. Copy .env.example to .env and fill it in."
        )

    client = OpenAI(base_url=base_url, api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )

    actual_model = response.model or model
    print(f"Model that answered: {actual_model}")

    choice = response.choices[0].message
    reply = (choice.content or "").strip()
    return reply, actual_model
