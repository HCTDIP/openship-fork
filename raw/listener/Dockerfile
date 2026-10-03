FROM python:3.12-alpine
WORKDIR /app
COPY listener.py report.py wallets.json ./
ENV LEDGER_DIR=/var/minis/shared/ledgers DURATION_H=72 TRACK=wallets PYTHONUNBUFFERED=1
CMD ["python", "listener.py"]
