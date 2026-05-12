-- PHANTOM READ
-- Открой два терминала с psql. Выполняй шаги по порядку.
-- Суть: Session A дважды считает строки по условию — получает разное количество.

-- ШАГ 1 — SESSION A:
begin;
set transaction isolation level read committed;
select count(*) from products where category = 'electronics';
-- видишь count = 2

-- ШАГ 2 — SESSION B:
begin;
insert into products (category, name, price) values ('electronics', 'Keyboard', 79.99);
commit;
select name, price from products where category = 'electronics';
-- видишь 3 строки

-- ШАГ 3 — SESSION A:
select count(*) from products where category = 'electronics';
-- видишь count = 3 — АНОМАЛИЯ!
-- тот же count(*) в той же транзакции вернул другое число
select name, price from products where category = 'electronics';
-- видишь новую строку Keyboard — это "фантом"
commit;
