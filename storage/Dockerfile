FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip config set global.trusted-host "pypi.org pypi.python.org files.pythonhosted.org"
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY provider.py api.py test_api.py ./

EXPOSE 5001
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5001", "api:app"]
