from app.config import Settings


def test_admin_ids_accept_a_comma_separated_environment_value(monkeypatch: object) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:token")
    monkeypatch.setenv("ADMIN_IDS", "100, 200")

    settings = Settings(_env_file=None)

    assert settings.admin_ids == (100, 200)
