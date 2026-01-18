import os
import time
from datetime import datetime, timedelta, timezone

import boto3
from sqlalchemy import create_engine, select, update, text
from sqlalchemy.orm import sessionmaker

from models import Base, OutboxEvent 

def _env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v

def db_url() -> str:
    return (
        "postgresql+psycopg2://"
        f"{_env('DB_USER')}:{_env('DB_PASSWORD')}"
        f"@{_env('DB_HOST')}:{_env('DB_PORT')}/{_env('DB_NAME')}"
    )

QUEUE_URL = _env("QUEUE_URL")
REGION = _env("AWS_REGION")

engine = create_engine(db_url(), pool_pre_ping=True, pool_size=3, max_overflow=5)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

sqs = boto3.client("sqs", region_name=REGION)

def main():
    print("publisher started")
    Base.metadata.create_all(bind=engine)

    while True:
        db = SessionLocal()
        try:
            # Core：FOR UPDATE SKIP LOCKED，avoiding rows being processed by other workers
            rows = db.execute(
                text("""
                SELECT id, payload
                FROM outbox_events
                WHERE status = 'pending'
                  AND available_at <= now()
                ORDER BY created_at
                LIMIT 10
                FOR UPDATE SKIP LOCKED
                """)
            ).fetchall()

            if not rows:
                db.commit()
                time.sleep(1.0)
                continue

            for (event_id, payload) in rows:
                try:
                    sqs.send_message(
                        QueueUrl=QUEUE_URL,
                        MessageBody=str(payload) if isinstance(payload, str) else __import__("json").dumps(payload),
                        MessageAttributes={
                            "producer": {"DataType": "String", "StringValue": "publisher"},
                        },
                    )
                    print(f"sent event {event_id} to SQS")
                    # mark published
                    db.execute(
                        text("""
                        UPDATE outbox_events
                        SET status='published',
                            published_at=now()
                        WHERE id=:id
                        """),
                        {"id": str(event_id)},
                    )
                except Exception as e:
                    # fail：attempts++ + backoff + pending
                    db.execute(
                        text("""
                        UPDATE outbox_events
                        SET attempts = attempts + 1,
                            available_at = now() + (:backoff || ' seconds')::interval
                        WHERE id=:id
                        """),
                        {"id": str(event_id), "backoff": 5},
                    )
                    print(f"send failed for {event_id}: {e}")

            db.commit()

        except Exception as e:
            db.rollback()
            print(f"loop error: {e}")
            time.sleep(2.0)
        finally:
            db.close()

if __name__ == "__main__":
    main()
