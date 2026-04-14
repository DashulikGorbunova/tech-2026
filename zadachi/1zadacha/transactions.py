import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "data/store.db")


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS Customers (
            CustomerID INTEGER PRIMARY KEY AUTOINCREMENT,
            FirstName TEXT NOT NULL,
            LastName TEXT NOT NULL,
            Email TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS Products (
            ProductID INTEGER PRIMARY KEY AUTOINCREMENT,
            ProductName TEXT NOT NULL,
            Price REAL NOT NULL CHECK(Price >= 0)
        );
        CREATE TABLE IF NOT EXISTS Orders (
            OrderID INTEGER PRIMARY KEY AUTOINCREMENT,
            CustomerID INTEGER NOT NULL,
            OrderDate TEXT NOT NULL,
            TotalAmount REAL DEFAULT 0 CHECK(TotalAmount >= 0),
            FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID)
        );
        CREATE TABLE IF NOT EXISTS OrderItems (
            OrderItemID INTEGER PRIMARY KEY AUTOINCREMENT,
            OrderID INTEGER NOT NULL,
            ProductID INTEGER NOT NULL,
            Quantity INTEGER NOT NULL CHECK(Quantity > 0),
            Subtotal REAL NOT NULL CHECK(Subtotal >= 0),
            FOREIGN KEY (OrderID) REFERENCES Orders(OrderID),
            FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
        );
    """)
    conn.commit()
    conn.close()


def place_order(customer_id, items):
    with get_connection() as conn:
        cursor = conn.cursor()
        order_date = datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO Orders (CustomerID, OrderDate, TotalAmount) VALUES (?, ?, 0)",
            (customer_id, order_date),
        )
        order_id = cursor.lastrowid
        total_amount = 0.0
        for item in items:
            product_id = item["product_id"]
            quantity = item["quantity"]
            cursor.execute("SELECT Price FROM Products WHERE ProductID = ?", (product_id,))
            result = cursor.fetchone()
            if result is None:
                raise ValueError(f"Product with ID {product_id} not found")
            price = result[0]
            subtotal = price * quantity
            total_amount += subtotal
            cursor.execute(
                "INSERT INTO OrderItems (OrderID, ProductID, Quantity, Subtotal) VALUES (?, ?, ?, ?)",
                (order_id, product_id, quantity, subtotal),
            )
        cursor.execute(
            "UPDATE Orders SET TotalAmount = ? WHERE OrderID = ?",
            (total_amount, order_id),
        )
        return order_id, total_amount


def update_customer_email(customer_id, new_email):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT CustomerID FROM Customers WHERE CustomerID = ?", (customer_id,))
        if cursor.fetchone() is None:
            raise ValueError(f"Customer with ID {customer_id} not found")
        cursor.execute(
            "UPDATE Customers SET Email = ? WHERE CustomerID = ?",
            (new_email, customer_id),
        )


def add_product(product_name, price):
    if price < 0:
        raise ValueError("Price cannot be negative")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Products (ProductName, Price) VALUES (?, ?)",
            (product_name, price),
        )
        return cursor.lastrowid


def main():
    init_db()

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM Customers")
        if cursor.fetchone()[0] == 0:
            cursor.executemany(
                "INSERT INTO Customers (FirstName, LastName, Email) VALUES (?, ?, ?)",
                [("Ivan", "Ivanov", "ivan@example.com"), ("Maria", "Petrova", "maria@example.com")],
            )
            cursor.executemany(
                "INSERT INTO Products (ProductName, Price) VALUES (?, ?)",
                [("Laptop", 50000), ("Mouse", 1500), ("Keyboard", 3000)],
            )

    order_id, total = place_order(
        customer_id=1,
        items=[
            {"product_id": 1, "quantity": 1},
            {"product_id": 2, "quantity": 2},
            {"product_id": 3, "quantity": 1},
        ],
    )
    print(f"Order #{order_id} created, total: {total}")

    update_customer_email(1, "ivan_new@example.com")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT Email FROM Customers WHERE CustomerID = 1")
        print(f"Updated email: {cursor.fetchone()[0]}")

    product_id = add_product("Monitor", 25000)
    print(f"Product added with ID #{product_id}")


if __name__ == "__main__":
    main()
