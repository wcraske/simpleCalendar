FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8000

CMD ["uvicorn", "calendarProject:app", "--host", "0.0.0.0", "--port", "8000"]

#verified working by running:
#docker build -t project .
#then
#docker run -p 8000:8000 project
