# Регистрация и заполнение анкеты

Последовательность от первого контакта с ботом до состояния «анкета заполнена, можно показывать в ленте». Имена сообщений и шагов ориентированы на сценарии из корневого README.

```mermaid
sequenceDiagram
    autonumber
    actor User as Пользователь
    participant Bot as Bot Service
    participant Auth as Auth Service
    participant Prof as Profile Service
    participant Media as Media Service
    participant Store as S3 / Minio

    User->>Bot: /start
    Bot->>Auth: создать/найти пользователя по Telegram ID
    Auth-->>Bot: user_id, сессия
    Bot->>Prof: создать черновик профиля
    Prof-->>Bot: profile_id

    loop Шаги анкеты
        Bot->>User: запрос поля (имя, возраст, пол, город…)
        User->>Bot: ответ
        Bot->>Prof: сохранить поле, обновить полноту
    end

    User->>Bot: загрузить фото
    Bot->>Media: запросить presigned URL
    Media->>Store: подготовить объект
    Media-->>Bot: URL для загрузки
    Bot-->>User: ссылка / инструкция
    User->>Store: PUT файла
    Bot->>Prof: зафиксировать storage_key, порядок фото

    Prof-->>Bot: profile_completeness, готовность к ленте
    Bot->>User: анкета готова / переход к просмотру
```

**Замечания по данным**

- После появления осмысленных полей анкеты сервис рейтингов может рассчитать **первичный** вклад (уровень 1); на диаграмме это не размазано по отдельным вызовам — на практике это отдельный запрос или событие `profile.updated` в очередь.
- Повторные правки анкеты повторяют цикл «запрос поля → Prof», без обязательного прохода через Auth.
