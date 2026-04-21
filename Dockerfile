FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nosy_nabo.py server.py ./
COPY templates/ templates/

EXPOSE 8000

CMD ["python", "server.py"]
