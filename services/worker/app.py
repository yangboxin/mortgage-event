import os
import time
import uuid
import json
import boto3

REGION = os.getenv("AWS_REGION", "us-east-1")
QUEUE_URL = os.environ["QUEUE_URL"]
BUCKET = os.environ["BUCKET"]
PREFIX = os.getenv("PREFIX", "raw")

sqs = boto3.client("sqs", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

def main():
    print(f"[worker] starting region={REGION} queue={QUEUE_URL} bucket={BUCKET} prefix={PREFIX}", flush=True)

    while True:
        resp = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=5,
            WaitTimeSeconds=20,     # long polling
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
            producer = None
            if isinstance(attrs, dict):
                prod = attrs.get("producer")
                if isinstance(prod, dict):
                    producer = prod.get("StringValue")
            print(f"[worker] received message; MessageAttributes={attrs}; producer={producer}; ReceiptHandle={receipt}", flush=True)
            try:
                event = json.loads(body)  # expected to be JSON
                if "payment_id" not in event:
                    raise ValueError("missing payment_id")

                # write to S3
                key = f"{PREFIX}/dt={time.strftime('%Y-%m-%d')}/{uuid.uuid4().hex}.json"
                s3.put_object(
                    Bucket=BUCKET,
                    Key=key,
                    Body=json.dumps(event).encode("utf-8"),
                    ContentType="application/json",
                )
                print(f"[worker] wrote s3://{BUCKET}/{key} event={event}", flush=True)

                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
                print("[worker] deleted message", flush=True)

            except Exception as e:
                # failed to process message, put to DLQ after max retries
                print(f"[worker] ERROR processing message: {e}; body={body}", flush=True)

if __name__ == "__main__":
    main()
