-- setup.sql
-- запускать один раз перед демонстрацией любой аномалии
-- psql: \i setup.sql

drop table if exists accounts;
drop table if exists products;
drop table if exists counters;

-- для dirty read и non-repeatable read
create table accounts (
    id   serial primary key,
    name varchar(50),
    balance numeric(10, 2)
);

insert into accounts (name, balance) values
    ('Alice', 1000.00),
    ('Bob',    500.00);

-- для phantom read
create table products (
    id       serial primary key,
    category varchar(50),
    name     varchar(100),
    price    numeric(10, 2)
);

insert into products (category, name, price) values
    ('electronics', 'Laptop', 999.99),
    ('electronics', 'Mouse',   29.99),
    ('books',       'Clean Code', 39.99);

-- для lost update
create table counters (
    id    int primary key,
    name  varchar(50),
    value int
);

insert into counters (id, name, value) values
    (1, 'page_views', 100);

select 'setup done' as status;
select * from accounts;
select * from products;
select * from counters;
