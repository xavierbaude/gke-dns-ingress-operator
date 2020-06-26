FROM python:3.7

WORKDIR /operator

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD kopf run --standalone handlers.py
