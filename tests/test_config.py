from pathlib import Path
import pytest
from app import config


def test_env_file_loading_and_bom(tmp_path,monkeypatch):
    env=tmp_path/'.env'
    env.write_text('# Local settings\nGEMINI_API_KEY="dummy-key"\nSUPERCHAT_CHAT_MODE=llm\n',encoding='utf-8-sig')
    monkeypatch.setattr(config,'ENV_FILE',env)
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    monkeypatch.delenv('SUPERCHAT_CHAT_MODE',raising=False)
    settings=config.Settings()
    assert settings.gemini_api_key=='dummy-key'
    assert settings.chat_mode=='llm'
    assert 'dummy-key' not in repr(settings)


def test_environment_wins_over_file(tmp_path,monkeypatch):
    env=tmp_path/'.env'
    env.write_text('GEMINI_API_KEY=file-value\n',encoding='utf-8')
    monkeypatch.setattr(config,'ENV_FILE',env)
    monkeypatch.setenv('GEMINI_API_KEY','terminal-value')
    assert config.Settings().gemini_api_key=='terminal-value'


def test_missing_file_uses_defaults(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'ENV_FILE',tmp_path/'missing')
    monkeypatch.delenv('SUPERCHAT_CHAT_MODE',raising=False)
    assert config.Settings().chat_mode=='rules'


def test_invalid_file_does_not_expose_value(tmp_path,monkeypatch):
    env=tmp_path/'.env'
    env.write_text('GEMINI_API_KEY="private-unclosed',encoding='utf-8')
    monkeypatch.setattr(config,'ENV_FILE',env)
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    with pytest.raises(ValueError) as result:config.Settings()
    assert 'private-unclosed' not in str(result.value)
