import importlib
import os

from pydantic import SecretStr

settings_module = importlib.import_module("app.config.settings")


def test_env_local_overrides_env(tmp_path):
    (tmp_path / ".env").write_text(
        "APIGEE_CLIENT_SECRET=from_dotenv\nAPIGEE_CLIENT_ID=dotenv-id\n", encoding="utf-8"
    )
    (tmp_path / ".env.local").write_text(
        "APIGEE_CLIENT_SECRET=from_local\nAPIGEE_CLIENT_ID=local-id\n", encoding="utf-8"
    )

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        importlib.reload(settings_module)
        secret_value = settings_module.settings.apigee_client_secret

        assert isinstance(secret_value, SecretStr)
        assert secret_value.get_secret_value() == "from_local"
        assert settings_module.settings.apigee_client_id == "local-id"
    finally:
        os.chdir(cwd)
        importlib.reload(settings_module)
