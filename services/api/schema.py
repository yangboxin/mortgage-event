from datetime import datetime
from pydantic import BaseModel, Field

class PaymentIn(BaseModel):
    payment_id: str = Field(min_length=1, max_length=64)
    amount: float
    ts: datetime

class PaymentOut(BaseModel):
    payment_id: str
    status: str
