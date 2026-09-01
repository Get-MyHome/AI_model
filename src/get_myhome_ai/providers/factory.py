from __future__ import annotations

from get_myhome_ai.fixtures import load_golden_cases
from get_myhome_ai.providers.base import ExtractorProvider
from get_myhome_ai.providers.fixture import FixtureExtractor
from get_myhome_ai.settings import Settings


def create_provider(settings: Settings) -> ExtractorProvider:
    if settings.ai_provider == "fixture":
        cases = load_golden_cases(settings.fixture_dir)
        return FixtureExtractor({key: case.expected for key, case in cases.items()})

    # Keep the OpenAI SDK out of keyless fixture-only runs.
    from get_myhome_ai.providers.openai import OpenAIExtractor

    return OpenAIExtractor(settings)
