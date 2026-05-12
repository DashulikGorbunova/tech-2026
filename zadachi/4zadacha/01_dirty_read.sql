-- DIRTY READ
-- Открой два терминала с psql. Выполняй шаги по порядку.
-- Суть: Session B пытается прочитать незакоммиченные данные Session A.
-- В PostgreSQL dirty read невозможен — PG покажет старое значение.

-- ШАГ 1 — SESSION A:
begin;
update accounts set balance = 0 where id = 1;
select id, name, balance from accounts where id = 1;
-- видишь balance = 0 (внутри своей транзакции)

-- ШАГ 2 — SESSION B:
begin;
set transaction isolation level read uncommitted;
select id, name, balance from accounts where id = 1;
-- ожидаешь увидеть 0, но видишь 1000.00 — PG защитил от dirty read

-- ШАГ 3 — SESSION A:
rollback;
select id, name, balance from accounts where id = 1;
-- баланс вернулся: 1000.00

-- ШАГ 4 — SESSION B:
commit;
