FROM python:3.12-slim

# Credentials are build args so they never land in an image layer
ARG ARTIFACTORY_USERNAME
ARG ARTIFACTORY_TOKEN

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --index-url "https://${ARTIFACTORY_USERNAME}:${ARTIFACTORY_TOKEN}@artifactory.marriott.com/artifactory/api/pypi/emergingtech-pypi-local/simple/" \
    --extra-index-url https://pypi.org/simple/ \
    -r requirements.txt

COPY agent.py .
EXPOSE 8080
CMD ["python", "agent.py"]
