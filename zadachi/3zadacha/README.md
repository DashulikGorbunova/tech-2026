# Задание 3: Сравнение типов кеширования

В этой папке реализован единый стенд для сравнения трех стратегий кеширования.

Состав стенда:
- генератор нагрузки: встроен в `benchmark.py`
- приложение: логика в классе `App` (`benchmark.py`)
- кеш: Redis (`docker-compose.yml`)
- БД: SQLite (`benchmark.sqlite`)

## Реализованные стратегии

1. `cache-aside` (lazy loading / write-around)
   - чтение через кеш
   - при cache miss данные читаются из БД и кладутся в кеш
   - запись идет сразу в БД, кеш по ключу инвалидируется

2. `write-through`
   - чтение через кеш
   - запись идет одновременно в кеш и БД

3. `write-back`
   - чтение через кеш
   - запись сначала в кеш
   - запись в БД выполняется асинхронно через flush-очередь

## Запуск

1) Поднять Redis:

```bash
docker compose up -d
```

2) Установить зависимости:

```bash
pip install -r requirements.txt
```

3) Запустить бенчмарк (рекомендуемый строгий режим: одинаковая длительность для всех профилей):

```bash
python benchmark.py --print-live --duration-sec 10
```

Альтернативный режим с фиксированным числом запросов:

```bash
python benchmark.py --print-live --total-requests 15000
```

Профили нагрузки:
- `read-heavy` (80% read / 20% write)
- `balanced` (50% read / 50% write)
- `write-heavy` (20% read / 80% write)

## Результаты

После прогона формируются:
- `results/results.csv`
- `results/report.md`
- `results/console.log`

Собираемые метрики:
- throughput (`req/sec`)
- средняя задержка (`avg_latency_ms`)
- обращения в БД (`db_reads`, `db_writes`)
- cache hit rate

Дополнительно для `write-back`:
- `max_write_back_queue`
