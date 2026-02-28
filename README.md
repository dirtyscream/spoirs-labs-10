# Network Labs

## Установка

```bash
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows
```

## Запуск

```bash
# Сервер (слушает 0.0.0.0:8888)
python -m network_labs

# Клиент (localhost)
python -m network_labs

# Клиент (удалённый сервер)
python -m network_labs 192.168.1.100
```

Сервер и клиент запускаются в разных терминалах.
