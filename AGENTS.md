# AI_model project rules

- Python 3.12 service. Install with `python -m pip install -e '.[dev]'`.
- Run `ruff check .`, `pytest`, and `python -m compileall -q src` before reporting completion.
- `POST /api/analyze` returns the canonical snake_case contract. Keep current-backend compatibility isolated in `contracts/backend_v1.py` and `/api/analyze/legacy`.
- All ratios are fractions of the total sale price (`0.40` means 40%). Never silently replace unknown values with zero.
- LLMs only extract facts and evidence. HOLD messages, summaries, validation, and financial calculations are deterministic Python code.
- Every extracted non-null decision field needs page evidence. Invalid or uncertain extraction must become HOLD, not a guessed value.
- Never log pre-signed PDF URLs, API keys, or full PDF text.
- Production OpenAI mode must fail readiness when `OPENAI_API_KEY` is missing; never fall back to fixtures automatically.

