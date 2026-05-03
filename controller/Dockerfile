FROM python:3.11-slim

LABEL maintainer="zta-project"
LABEL description="ZTA Continuous Attestation Engine for Kubernetes"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY zta_engine.py .

RUN mkdir -p /var/log/zta

RUN useradd -m -u 1000 ztauser
USER ztauser

CMD ["python3", "zta_engine.py"]

