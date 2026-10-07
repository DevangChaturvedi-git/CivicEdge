FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY civicedge ./civicedge
COPY config ./config
COPY experiments ./experiments
ENV PYTHONUNBUFFERED=1
