FROM python:3.10
WORKDIR /app
COPY backend/ /app
RUN pip install -r requirements.txt
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port $PORT"]


