from app.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("NEWSREAD_JWT_SECRET", raising=False)
    s = Settings(_env_file=None)
    assert s.jwt_expires_days == 30
    assert s.feed_refresh_minutes == 15
    assert "postgresql" in s.database_url


def test_openai_alias_newsread_prefix(monkeypatch):
    monkeypatch.setenv("NEWSREAD_OPENAI_API_KEY", "from-newsread")
    s = Settings(_env_file=None)
    assert s.openai_api_key == "from-newsread"


def test_openai_alias_standard_name(monkeypatch):
    monkeypatch.delenv("NEWSREAD_OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-x")
    s = Settings(_env_file=None)
    assert s.openai_model == "gpt-x"


def test_extra_env_ignored(monkeypatch):
    monkeypatch.setenv("NEWSREAD_TOTALLY_UNKNOWN", "x")
    Settings(_env_file=None)  # extra="ignore" -> no error
