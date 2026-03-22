# Лента анкет и свайпы (лайк / пропуск)

Как пользователь получает карточки и какие записи и события появляются при действиях. Акцент на связке **Feed + Redis + Interaction + MQ**, как в архитектурном описании.

```mermaid
sequenceDiagram
    autonumber
    actor User as Пользователь
    participant Bot as Bot Service
    participant Feed as Feed Service
    participant Redis as Redis
    participant Rank as Rating Service
    participant Prof as Profile Service
    participant Int as Interaction Service
    participant MQ as Очередь сообщений

    User->>Bot: открыть ленту / «следующая»
    Bot->>Feed: запрос следующей анкеты для viewer_id
    Feed->>Redis: взять префетч-пачку
    alt пачка есть и актуальна
        Redis-->>Feed: profile_ids[]
    else пачка пуста или протухла
        Feed->>Rank: запрос ранжирования / фильтров
        Rank->>Prof: отбор кандидатов
        Prof-->>Rank: данные для скоринга
        Rank-->>Feed: упорядоченный список
        Feed->>Redis: сохранить пачку N карточек
    end
    Feed-->>Bot: одна карточка + метаданные
    Bot-->>User: сообщение с фото и кнопками

    User->>Bot: лайк или пропуск
    Bot->>Int: зафиксировать действие
    Int->>Int: запись в БД
    Int->>MQ: событие profile.liked / profile.skipped
    Note over MQ,Rank: Rating Service и воркеры подписаны на поток
    Int-->>Bot: результат (есть ли мэтч)
    alt взаимный лайк
        Bot-->>User: уведомление о мэтче
    end
```

**Идея кэша**

- Первая карточка может считаться «в лоб», следующие — из **предзагруженной** очереди в Redis, чтобы не блокировать чат на тяжёлом ранжировании при каждом свайпе.
- При изменении рейтинга пачка может инвалидироваться по TTL или по событию пересчёта — детали политики кэша остаются на этапе реализации.
