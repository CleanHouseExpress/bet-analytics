from fastapi.testclient import TestClient

from apps.api.app.main import APP_NAME, APP_VERSION, app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version() -> None:
    response = client.get("/version")

    assert response.status_code == 200
    assert response.json() == {"name": APP_NAME, "version": APP_VERSION}
