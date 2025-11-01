from app.config.settings import Settings


def test_env_local_overrides_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_local_file = tmp_path / ".env.local"
    env_file.write_text("APIGEE_CLIENT_SECRET=from_env\n", encoding="utf-8")
    env_local_file.write_text("APIGEE_CLIENT_SECRET=from_local\n", encoding="utf-8")

    monkeypatch.delenv("APIGEE_CLIENT_SECRET", raising=False)
    monkeypatch.setitem(
        Settings.model_config,
        "env_file",
        (str(env_file), str(env_local_file)),
    )

    test_settings = Settings()

    assert test_settings.apigee_client_secret is not None
    assert test_settings.apigee_client_secret.get_secret_value() == "from_local"
