# bet-analytics

Base inicial do projeto de análise quantitativa e recomendação de apostas esportivas.

## Stack inicial

- Python 3.13
- FastAPI
- Uvicorn
- Pydantic
- Pytest
- HTTPX
- Ruff
- Docker

## Execução local

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn apps.api.app.main:app --reload
```

API: http://localhost:8000

Endpoints iniciais:

- `GET /health`
- `GET /version`

## Testes

```bash
pytest
```

## Qualidade

```bash
ruff check .
ruff format --check .
```

## Docker

```bash
docker build -t bet-analytics-api .
docker run --rm -p 8000:8000 bet-analytics-api
```
