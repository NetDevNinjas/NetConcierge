FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY perk_agent.py .
COPY profiles/ ./profiles/
EXPOSE 8081
CMD ["python", "perk_agent.py"]
