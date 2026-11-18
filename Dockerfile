FROM python:3.11-slim
WORKDIR /app
COPY . .
CMD ["python","-m","battery_cooperation.cli","data/example.json"]
