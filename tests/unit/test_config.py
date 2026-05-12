from __future__ import annotations

from app.core.config import (
    BackendConfig,
    load_config,
)


def test_backend_config_has_credentials_for_api_key_auth():
    cfg = BackendConfig(type="search_router", auth_type="api_key", resolved_api_key="k")
    assert cfg.has_credentials is True


def test_backend_config_missing_api_key_means_no_credentials():
    cfg = BackendConfig(type="search_router", auth_type="api_key")
    assert cfg.has_credentials is False


def test_backend_config_iam_auth_only_inspects_iam_token():
    """Even if api_key is set, ``iam`` auth requires an IAM token to count as credentialed."""
    cfg = BackendConfig(
        type="search_router",
        auth_type="iam",
        resolved_api_key="leftover",
        resolved_iam_token=None,
    )
    assert cfg.has_credentials is False

    cfg.resolved_iam_token = "tok"
    assert cfg.has_credentials is True


def test_load_config_returns_defaults_when_file_missing(tmp_path):
    """Pointing at a non-existent file must fall back to all defaults, not raise."""
    config = load_config(tmp_path / "nope.yaml", env={})
    assert config.app.name == "search-service"
    assert config.search.backends == {}


def test_load_config_resolves_credentials_from_env(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        """
search:
  backends:
    search_router:
      type: search_router
      auth_type: api_key
      api_key_env: MY_API_KEY
""",
        encoding="utf-8",
    )
    config = load_config(yaml_path, env={"MY_API_KEY": "secret"})
    cfg = config.search.backends["search_router"]
    assert cfg.resolved_api_key == "secret"
    assert cfg.has_credentials is True


def test_load_config_treats_empty_env_var_as_missing(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        """
search:
  backends:
    search_router:
      type: search_router
      auth_type: api_key
      api_key_env: KEY
""",
        encoding="utf-8",
    )
    config = load_config(yaml_path, env={"KEY": ""})
    cfg = config.search.backends["search_router"]
    assert cfg.resolved_api_key is None


def test_load_config_resolves_redis_url_from_env(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "cache:\n  enabled: true\n  redis_url_env: REDIS\n",
        encoding="utf-8",
    )
    config = load_config(yaml_path, env={"REDIS": "redis://localhost:6379/0"})
    assert config.cache.resolved_redis_url == "redis://localhost:6379/0"
