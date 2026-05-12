-- LOST UPDATE
-- Открой два терминала с psql. Выполняй шаги по порядку.
-- Суть: обе сессии читают одно значение, обе записывают +1.
-- Итог: +1 вместо +2. Одно обновление потеряно.

-- перед стартом:
select * from counters where id = 1;
-- value = 100

-- ШАГ 1 — SESSION A:
begin;
select value from counters where id = 1;
-- видишь value = 100, запоминаешь: буду писать 101

-- ШАГ 2 — SESSION B:
begin;
select value from counters where id = 1;
-- тоже видишь value = 100, тоже запоминаешь: буду писать 101

-- ШАГ 3 — SESSION B:
update counters set value = 101 where id = 1;
commit;
select value from counters where id = 1;
-- видишь value = 101

-- ШАГ 4 — SESSION A:
update counters set value = 101 where id = 1;
commit;
select value from counters where id = 1;
-- видишь value = 101 — АНОМАЛИЯ!
-- должно быть 102, но обновление Session B перезаписано
