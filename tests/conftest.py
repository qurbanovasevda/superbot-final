"""Keep unit tests offline even when the user's terminal has an API key."""
import pytest


@pytest.fixture(autouse=True)
def offline_test_environment(monkeypatch):
    monkeypatch.setenv('SUPERCHAT_CHAT_MODE','rules')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setenv('GEMINI_API_KEY','')

    monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "")

    monkeypatch.setenv('SUPERCHAT_FLIGHT_PROVIDER', 'travelpayouts')
