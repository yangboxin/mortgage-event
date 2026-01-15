from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os, json, boto3, uuid, datetime

app = FastAPI()

REGION = os.getenv("AWS_REGION", "us-east-1")
QUEUE_URL = os.getenv("QUEUE_URL")

sqs = boto3.client("sqs", region_name=REGION)

class PaymentIn(BaseModel):
    payment_id: str | None = None
    amount: float
    ts: str | None = None

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/payments")
def create_payment(p: PaymentIn):
    if not QUEUE_URL:
        raise HTTPException(status_code=500, detail="QUEUE_URL not set")

    pid = p.payment_id or f"p-{uuid.uuid4().hex[:10]}"
    ts = p.ts or datetime.datetime.utcnow().isoformat() + "Z"

    event = {"payment_id": pid, "amount": p.amount, "ts": ts}
    sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(event))
    return {"enqueued": True, "payment_id": pid}
