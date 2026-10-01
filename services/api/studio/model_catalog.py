"""Operator-visible OpenAI model choices and conservative standard text rates.

Rates are USD per million tokens, checked against official model pages 2026-10-01.
Aliases may move; paid jobs store the model returned in the provider receipt.
"""

MODEL_CATALOG = {
    "gpt-6.1-sol": {"label": "GPT-6.1 Sol · đề xuất", "input_rate": 2.0, "output_rate": 10.0,
                    "cache_write_rate": 2.5, "reasoning_effort": "low", "legacy": False},
    "gpt-6-astra": {"label": "GPT-6 Astra · chất lượng cao", "input_rate": 10.0, "output_rate": 50.0,
                    "cache_write_rate": 12.5, "reasoning_effort": "low", "legacy": False},
    "gpt-6-luna": {"label": "GPT-6 Luna · tiết kiệm", "input_rate": 0.1, "output_rate": 0.5,
                     "cache_write_rate": 0.125, "reasoning_effort": "none", "legacy": False},
    "gpt-4o-mini-2024-07-18": {"label": "GPT-4o mini · cũ", "input_rate": 0.15, "output_rate": 0.6,
                                 "cache_write_rate": 0.15, "reasoning_effort": None, "legacy": True},
    "gpt-4o-2024-08-06": {"label": "GPT-4o · cũ", "input_rate": 2.5, "output_rate": 10.0,
                            "cache_write_rate": 2.5, "reasoning_effort": None, "legacy": True},
}

DEFAULT_MODEL = "gpt-6.1-sol"


def public_models() -> list[dict]:
    return [{"id": model, "label": info["label"], "input_rate": info["input_rate"],
             "output_rate": info["output_rate"], "legacy": info["legacy"]}
            for model, info in MODEL_CATALOG.items()]
