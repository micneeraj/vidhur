import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import ask_llm


def test_hello_from_vidhur():
    reply, model_name = ask_llm("Say hello from Vidhur")
    print(f"Reply: {reply}")
    print(f"Model: {model_name}")
    assert reply, "Expected a non-empty reply from the LLM"
    return reply, model_name


if __name__ == "__main__":
    test_hello_from_vidhur()
