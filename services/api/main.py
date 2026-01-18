from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

from db import SessionLocal, engine
from models import Base, Payment, OutboxEvent
from schema import PaymentIn, PaymentOut

app = FastAPI()

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/payments", response_model=PaymentOut)
def create_payment(p: PaymentIn):
    db = SessionLocal()
    try:
        pay = Payment(payment_id=p.payment_id, amount=p.amount, ts=p.ts)
        db.add(pay)

        evt = OutboxEvent(
            aggregate_type="payment",
            aggregate_id=p.payment_id,
            event_type="PaymentCreated",
            payload=p.model_dump(mode="json"),   # jsonb
            status="pending",
        )
        db.add(evt)

        db.commit()
        return PaymentOut(payment_id=p.payment_id, status="accepted")

    except IntegrityError:
        db.rollback()
        # payment_id already exists
        raise HTTPException(status_code=409, detail="payment_id already exists")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
