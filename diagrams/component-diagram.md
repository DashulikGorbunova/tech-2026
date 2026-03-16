## Диаграмма компонентов (Component Diagram)

Высокоуровневое представление сервисов, инфраструктуры и связей между ними.

```mermaid
flowchart TB
    subgraph external["Внешние системы"]
        TG[Telegram API]
    end

    subgraph services["Сервисы приложения"]
        BOT[Bot Service]
        AUTH[Auth / User Service]
        PROFILE[Profile Service]
        MATCH[Interaction / Match Service]
        RANK[Rating Service]
        FEED[Feed / Recommendation Service]
        DIALOG[Dialog / Chat Service]
        MEDIA[Media Service]
        CELERY[Celery Workers]
    end

    subgraph infrastructure["Инфраструктура"]
        PG[(PostgreSQL)]
        REDIS[(Redis)]
        MQ[RabbitMQ]
        MINIO[(MinIO)]
        PROM[Prometheus]
        GRAF[Grafana]
    end

    TG <--> BOT

    BOT -->|"HTTP REST"| AUTH
    BOT -->|"HTTP REST"| PROFILE
    BOT -->|"HTTP REST"| MATCH
    BOT -->|"HTTP REST"| FEED
    BOT -->|"HTTP REST"| DIALOG

    AUTH --> PG
    PROFILE --> PG
    MATCH --> PG
    RANK --> PG
    DIALOG --> PG
    CELERY --> PG

    PROFILE --> MINIO
    MEDIA --> MINIO

    RANK --> REDIS
    FEED --> REDIS

    AUTH -->|"publish events"| MQ
    MATCH -->|"publish events"| MQ
    DIALOG -->|"publish events"| MQ

    MQ -->|"consume events"| RANK
    MQ -->|"consume events"| BOT
    MQ -->|"consume events"| CELERY

    RANK --> CELERY

    BOT -->|"metrics"| PROM
    AUTH -->|"metrics"| PROM
    PROFILE -->|"metrics"| PROM
    MATCH -->|"metrics"| PROM
    RANK -->|"metrics"| PROM
    FEED -->|"metrics"| PROM
    DIALOG -->|"metrics"| PROM
    CELERY -->|"metrics"| PROM

    PROM --> GRAF
```

## Легенда

| Тип связи | Описание |
|----------|----------|
| HTTP REST | Синхронные запросы (регистрация, анкеты, свайпы, лента, диалоги) |
| MQ events | Асинхронные события (`profile.liked`, `profile.skipped`, `match.created`, `dialog.started`, `message.sent`) |
| metrics | Экспорт метрик для Prometheus |


