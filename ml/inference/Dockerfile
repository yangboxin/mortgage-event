FROM python:3.11-slim

WORKDIR /app
RUN pip install fastapi uvicorn

COPY serve.py .

EXPOSE 8080
CMD ["python", "serve.py"]
