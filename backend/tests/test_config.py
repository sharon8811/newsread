import pytest
from pydantic import ValidationError

from app.config import DeploymentMode, Settings


def _clear_deployment_env(monkeypatch):
    """conftest pins these for the suite; derivation tests need them unset."""
    for var in ("NEWSREAD_DEPLOYMENT", "NEWSREAD_ALLOW_SIGNUP", "NEWSREAD_MESSAGING_ENABLED"):
        monkeypatch.delenv(var, raising=False)


def test_self_hosted_is_default_and_closes_signup_and_messaging(monkeypatch):
    _clear_deployment_env(monkeypatch)
    monkeypatch.delenv("NEWSREAD_JWT_SECRET", raising=False)
    s = Settings(_env_file=None)
    assert s.deployment is DeploymentMode.self_hosted
    assert s.allow_signup is False
    assert s.messaging_enabled is False


def test_prod_defaults_open_signup_and_messaging(monkeypatch):
    _clear_deployment_env(monkeypatch)
    s = Settings(_env_file=None, deployment="prod", jwt_secret="real-secret")
    assert s.allow_signup is True
    assert s.messaging_enabled is True


def test_staging_matches_prod_defaults(monkeypatch):
    _clear_deployment_env(monkeypatch)
    s = Settings(_env_file=None, deployment="staging", jwt_secret="real-secret")
    assert s.allow_signup is True
    assert s.messaging_enabled is True


def test_explicit_flag_beats_mode_default(monkeypatch):
    _clear_deployment_env(monkeypatch)
    s = Settings(
        _env_file=None,
        deployment="prod",
        jwt_secret="real-secret",
        allow_signup=False,
        messaging_enabled=False,
    )
    assert s.allow_signup is False
    assert s.messaging_enabled is False
    hosted = Settings(_env_file=None, allow_signup=True, messaging_enabled=True)
    assert hosted.allow_signup is True
    assert hosted.messaging_enabled is True


@pytest.mark.parametrize("mode", ["prod", "staging"])
def test_non_self_hosted_refuses_dev_jwt_secret(monkeypatch, mode):
    _clear_deployment_env(monkeypatch)
    monkeypatch.delenv("NEWSREAD_JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="NEWSREAD_JWT_SECRET"):
        Settings(_env_file=None, deployment=mode)


def test_self_hosted_tolerates_dev_jwt_secret(monkeypatch):
    _clear_deployment_env(monkeypatch)
    monkeypatch.delenv("NEWSREAD_JWT_SECRET", raising=False)
    assert Settings(_env_file=None).jwt_secret == "dev-secret-change-me"


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
