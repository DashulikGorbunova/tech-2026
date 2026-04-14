FROM python:3.12-slim

WORKDIR /app

# Создаём директорию для данных
RUN mkdir -p /app/data

# Копируем скрипт
COPY transactions.py .
COPY init.sql .

# Переменная окружения для пути к БД
ENV DB_PATH=/app/data/store.db

# Запускаем демо
CMD ["python", "transactions.py"]
