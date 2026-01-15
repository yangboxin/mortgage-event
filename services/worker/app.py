import os
import time
import uuid
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
        )
        msgs = resp.get("Messages", [])
        if not msgs:
            continue

        for m in msgs:
            body = m.get("Body", "")
            receipt = m["ReceiptHandle"]

            key = f"{PREFIX}/dt={time.strftime('%Y-%m-%d')}/{uuid.uuid4().hex}.json"
            s3.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/json",
            )
            print(f"[worker] wrote s3://{BUCKET}/{key} body={body}", flush=True)

            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
            print("[worker] deleted message", flush=True)

if __name__ == "__main__":
    main()
