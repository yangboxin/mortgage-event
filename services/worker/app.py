import os
import time
import json
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError

REGION = os.getenv("AWS_REGION", "us-east-1")
QUEUE_URL = os.environ["QUEUE_URL"]
BUCKET = os.environ["BUCKET"]

RAW_PREFIX = os.getenv("PREFIX", "raw")              # raw
QUAR_PREFIX = os.getenv("QUAR_PREFIX", "quarantine") # quarantine

sqs = boto3.client("sqs", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

def parse_dt(event: dict) -> str:
    """
    Prefer event time partition (UTC date). Fallback to ingest date.
    Expect event['ts'] like '2026-01-18T05:00:00Z' or ISO8601.
    """
    ts = event.get("ts")
    if isinstance(ts, str):
        try:
            # handle trailing Z
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            dt = datetime.fromisoformat(ts).astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return time.strftime("%Y-%m-%d")

def get_producer(attrs: dict) -> str | None:
    prod = attrs.get("producer") if isinstance(attrs, dict) else None
    if isinstance(prod, dict):
        return prod.get("StringValue")
    return None

def put_json(bucket: str, key: str, obj: dict) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

def main():
    print(f"[worker] starting region={REGION} queue={QUEUE_URL} bucket={BUCKET} raw={RAW_PREFIX} quar={QUAR_PREFIX}", flush=True)

    while True:
        resp = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=5,
            WaitTimeSeconds=20,
            VisibilityTimeout=60,
            MessageAttributeNames=["All"],
        )

        msgs = resp.get("Messages", [])
        if not msgs:
            continue

        for m in msgs:
            body = m.get("Body", "")
            receipt = m["ReceiptHandle"]
            attrs = m.get("MessageAttributes", {})
            producer = get_producer(attrs)

            try:
                event = json.loads(body)
                if not isinstance(event, dict):
                    raise ValueError("event is not an object")

                payment_id = event.get("payment_id")
                if not isinstance(payment_id, str) or not payment_id:
                    raise ValueError("missing/invalid payment_id")

                dt = parse_dt(event)

                # add ingestion metadata (helps debugging)
                enriched = dict(event)
                enriched["_meta"] = {
                    "ingested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "producer": producer,
                }

                # idempotent-ish raw key: one file per payment_id per day
                key = f"{RAW_PREFIX}/payments/dt={dt}/payment_id={payment_id}.json"

                put_json(BUCKET, key, enriched)
                print(f"[worker] wrote s3://{BUCKET}/{key} producer={producer}", flush=True)

                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
                print("[worker] deleted message", flush=True)

            except ValueError as e:
                # Bad data: quarantine + delete (no point retrying)
                dt_ingest = time.strftime("%Y-%m-%d")
                qkey = f"{QUAR_PREFIX}/dt={dt_ingest}/{int(time.time())}.json"
                try:
                    put_json(BUCKET, qkey, {
                        "error": str(e),
                        "body": body,
                        "attributes": attrs,
                        "received_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    })
                    print(f"[worker] quarantined s3://{BUCKET}/{qkey} error={e}", flush=True)
                except Exception as qe:
                    # If even quarantine fails, do NOT delete message; let it retry / DLQ
                    print(f"[worker] ERROR quarantine failed: {qe}; original_error={e}", flush=True)
                    continue

                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)

            except ClientError as e:
                # AWS transient / permission / throttling - do not delete, allow retry / DLQ
                print(f"[worker] AWS ERROR: {e}; will retry; producer={producer}", flush=True)
                continue

            except Exception as e:
                # Unknown error - do not delete, allow retry / DLQ
                print(f"[worker] ERROR processing message: {e}; will retry; producer={producer}", flush=True)
                continue

if __name__ == "__main__":
    main()
