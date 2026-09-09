from fastapi.testclient import TestClient

from apps.api.app.main import APP_NAME, APP_VERSION, app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_database_health(monkeypatch) -> None:
    monkeypatch.setattr("apps.api.app.main.check_database", lambda: True)

    response = client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_database_health_unavailable(monkeypatch) -> None:
    def unavailable() -> bool:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("apps.api.app.main.check_database", unavailable)

    response = client.get("/health/db")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


def test_version() -> None:
    response = client.get("/version")

    assert response.status_code == 200
    assert response.json() == {"name": APP_NAME, "version": APP_VERSION}
