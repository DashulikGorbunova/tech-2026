-- NON-REPEATABLE READ
-- Открой два терминала с psql. Выполняй шаги по порядку.
-- Суть: Session A дважды читает одну строку и получает разные значения.

-- ШАГ 1 — SESSION A:
begin;
set transaction isolation level read committed;
select id, name, balance from accounts where id = 1;
-- видишь balance = 1000.00

-- ШАГ 2 — SESSION B:
begin;
update accounts set balance = balance + 100 where id = 1;
commit;
select id, name, balance from accounts where id = 1;
-- видишь balance = 1100.00

-- ШАГ 3 — SESSION A:
select id, name, balance from accounts where id = 1;
-- видишь balance = 1100.00 — АНОМАЛИЯ!
-- тот же select в той же транзакции вернул другое значение
commit;
