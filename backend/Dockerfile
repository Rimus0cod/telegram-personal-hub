FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
CMD ["sh","-c","alembic -c /app/alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
