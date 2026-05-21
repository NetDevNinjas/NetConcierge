FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY perk_agent.py .
COPY customer_profile.txt .
EXPOSE 8081
CMD ["python", "perk_agent.py"]
