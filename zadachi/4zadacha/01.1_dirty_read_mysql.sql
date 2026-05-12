-- DIRTY READ — MySQL
-- MySQL с движком InnoDB поддерживает READ UNCOMMITTED, что позволяет воспроизвести dirty read.
-- Открой два терминала с mysql. Выполняй шаги по порядку.
-- Суть: Session B читает незакоммиченные данные Session A.

-- SETUP (один раз, если ещё не запускал):
-- create database isolation_demo;
-- use isolation_demo;
-- create table accounts (id int primary key, name varchar(50), balance decimal(10,2));
-- insert into accounts values (1, 'Alice', 1000.00);

-- ШАГ 1 — SESSION A:
use isolation_demo;
begin;
update accounts set balance = 0 where id = 1;
select id, name, balance from accounts where id = 1;
-- видишь balance = 0 (внутри своей транзакции, изменение не закоммичено)

-- ШАГ 2 — SESSION B (не ждать коммита от Session A!):
use isolation_demo;
set session transaction isolation level read uncommitted;
begin;
select id, name, balance from accounts where id = 1;
-- видишь 0 — это DIRTY READ: Session B прочитала незакоммиченные данные Session A

-- ШАГ 3 — SESSION A:
rollback;
-- Session A откатила изменения, баланс снова 1000.00

-- ШАГ 4 — SESSION B:
select id, name, balance from accounts where id = 1;
-- теперь видишь 1000.00 — те данные, которые Session B читала на шаге 2, были "грязными"
commit;

-- ВЫВОД:
-- PostgreSQL: даже при READ UNCOMMITTED возвращает закоммиченные данные — dirty read невозможен.
-- MySQL InnoDB: при READ UNCOMMITTED реально возвращает незакоммиченные данные — dirty read происходит.
