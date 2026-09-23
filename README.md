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

### Docker Compose

```bash
docker compose up -d
```

No Compose, o PostgreSQL fica disponível apenas na rede interna como `db:5432`.
A porta `5432` **não é publicada no host por padrão**, portanto outro PostgreSQL
local pode continuar usando essa porta sem conflito.

A API do Compose usa:

```text
postgresql+psycopg://bet_analytics:bet_analytics@db:5432/bet_analytics
```

Para administração local do banco do Compose, prefira executar o cliente dentro
do container, por exemplo:

```bash
docker compose exec db psql -U bet_analytics -d bet_analytics
```

O volume nomeado `postgres_data` continua responsável pela persistência e não
depende de publicação de porta no host.
