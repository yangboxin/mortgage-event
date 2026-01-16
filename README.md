# Mortgage Event Processing Pipeline

A serverless event-driven architecture for processing mortgage payment events on AWS. This system provides a REST API to receive payment events, stores them in a queue, and processes them asynchronously to persist data in S3.

## Architecture Overview

```
┌──────────────────┐
│   API Service    │
│  (FastAPI)       │
└────────┬─────────┘
         │ HTTP POST /payments
         ▼
┌──────────────────┐      ┌──────────────────┐
│   ALB (ELB)      │      │   SQS Queue      │
└────────┬─────────┘      │  (with DLQ)      │
         │                └────────┬─────────┘
         └────────────────────────┤
                                  ▼
                        ┌──────────────────┐
                        │ Worker Service   │
                        │  (Python)        │
                        └────────┬─────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │   S3 Bucket      │
                        │  (KMS encrypted) │
                        └──────────────────┘
```

## Project Structure

```
mortgage-event/
├── services/
│   ├── api/                  # FastAPI service
│   │   ├── main.py          # REST API endpoints
│   │   ├── Dockerfile       # API container
│   │   └── requirements.txt
│   └── worker/              # Worker service
│       ├── app.py           # SQS message processor
│       ├── Dockerfile       # Worker container
│       └── requirements.txt
├── infra/                    # AWS CDK Infrastructure
│   ├── app.py               # CDK app entry point
│   ├── cdk.json             # CDK configuration
│   ├── requirements.txt      # Python dependencies
│   └── infra/
│       ├── infra_stack.py   # CloudFormation stack definition
│       └── stacks/
│           └── base_stack.py # Base infrastructure (VPC, ECS, SQS, S3, etc.)
├── payment.json             # Sample payment event
└── README.md
```

## Components

### 1. API Service (`services/api/`)

A FastAPI application that exposes HTTP endpoints for payment submissions.

**Endpoint:**
- `POST /payments` - Submit a new payment event
- `GET /health` - Health check

**Request Body:**
```json
{
  "payment_id": "p123",  // optional, auto-generated if not provided
  "amount": 99.12,
  "ts": "2026-01-15T12:00:00Z"  // optional, uses UTC now if not provided
}
```

**Response:**
```json
{
  "enqueued": true,
  "payment_id": "p123"
}
```

**Environment Variables:**
- `AWS_REGION` (default: `us-east-1`)
- `QUEUE_URL` - SQS queue URL (injected by CDK)

### 2. Worker Service (`services/worker/`)

A background worker that processes payment events from the SQS queue.

**Process Flow:**
1. Long polls SQS queue (20s timeout)
2. Receives up to 5 messages at a time
3. Validates message contains `payment_id`
4. Writes event to S3 in format: `s3://bucket/raw/dt=YYYY-MM-DD/uuid.json`
5. Deletes message from queue
6. Failed messages automatically moved to DLQ after 5 retries

**Environment Variables:**
- `AWS_REGION` (default: `us-east-1`)
- `QUEUE_URL` - SQS queue URL
- `BUCKET` - S3 bucket name
- `PREFIX` (default: `raw`) - S3 folder prefix

### 3. Infrastructure (`infra/`)

AWS CDK stack that defines all cloud resources:

**Resources Created:**
- **VPC**: 2 availability zones with NAT gateway
- **ECS Cluster**: Fargate-based containerized services
- **SQS**: Payment queue with Dead Letter Queue (DLQ)
  - Visibility timeout: 60s
  - Retention: 4 days
  - Max receive count: 5 (before moving to DLQ)
  - DLQ retention: 14 days
- **S3 Bucket**: SSE-KMS encrypted with auto-delete on stack removal
- **KMS CMK**: Customer-managed encryption key with automatic rotation
- **ALB**: Application Load Balancer for API service (HTTP on port 80)
- **IAM Roles**: Task execution roles with minimal permissions
- **CloudWatch Logs**: 1-week retention for all services
- **ECR Repositories**: For storing API and Worker container images

## Deployment

### Prerequisites
- AWS account with appropriate permissions
- Docker installed
- AWS CDK CLI: `npm install -g aws-cdk`
- Python 3.11+
- Node.js 16+

### Setup Infrastructure

```bash
cd infra

# Create Python virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Synthesize CloudFormation template
cdk synth

# Deploy to AWS
cdk deploy
```

### Build and Push Docker Images

```powershell
# Set variables
$AWS_ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
$AWS_REGION = "us-east-1"

# Login to ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# Build and push API image
cd services/api
docker build -t mortgage-api:latest .
docker tag mortgage-api:latest "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-api:latest"
docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-api:latest"

# Build and push Worker image
cd ../worker
docker build -t mortgage-worker:latest .
docker tag mortgage-worker:latest "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-worker:latest"
docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-worker:latest"
```

### Update ECS Services

```powershell
# Get cluster and service names
$CLUSTER = (aws ecs list-clusters --query 'clusterArns[0]' --output text).Split('/')[-1]
$SERVICE = (aws ecs list-services --cluster $CLUSTER --query 'serviceArns[0]' --output text).Split('/')[-1]

# Force new deployment with latest images
aws ecs update-service --cluster $CLUSTER --service $SERVICE --force-new-deployment
```

## Testing

### Send Test Payment via API

```powershell
# Option 1: Using PowerShell ConvertTo-Json
$payload = @{
  payment_id = "test-001"
  amount = 150.00
  ts = "2026-01-15T12:00:00Z"
} | ConvertTo-Json -Compress

Invoke-RestMethod -Method Post `
  -Uri "http://$ALB_DNS/payments" `
  -ContentType "application/json" `
  -Body $payload

# Option 2: Using AWS CLI
aws sqs send-message `
  --queue-url $QUEUE_URL `
  --message-body '{"payment_id":"test-002","amount":99.99,"ts":"2026-01-15T12:00:00Z"}'
```

### Monitor Logs

```powershell
# View recent logs from API service
aws logs tail /ecs/mortgage-worker-logs --follow

# View specific error messages
aws logs tail /ecs/mortgage-worker-logs --grep "ERROR"
```

### Check S3 Data

```powershell
# List all processed payments
aws s3 ls s3://$BUCKET/raw/ --recursive

# List today's payments
$DATE = Get-Date -Format "yyyy-MM-dd"
aws s3 ls "s3://$BUCKET/raw/dt=$DATE/" --recursive
```

### Monitor Queue Health

```powershell
# Get queue metrics
aws sqs get-queue-attributes `
  --queue-url $QUEUE_URL `
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed

# Get DLQ metrics
aws sqs get-queue-attributes `
  --queue-url $DLQ_URL `
  --attribute-names ApproximateNumberOfMessages
```

### Clean up DLQ

```powershell
# Purge all messages from DLQ (WARNING: messages will be deleted permanently)
aws sqs purge-queue --queue-url $DLQ_URL
```

## Configuration

### Environment-Specific Settings

Update CDK context in `infra/cdk.json`:
```json
{
  "env": {
    "account": "123456789012",
    "region": "us-east-1"
  }
}
```

### Task Resources

Modify in `infra/infra/stacks/base_stack.py`:
- API: 256 CPU units, 512 MB memory (Fargate)
- Worker: Modify similar settings for worker task definition

### SQS Queue Settings

All queue settings in `infra/infra/stacks/base_stack.py`:
- `visibility_timeout`: Message processing time limit
- `retention_period`: How long to keep messages
- `max_receive_count`: Retries before DLQ

## Troubleshooting

### Print Statements Not Appearing in Logs
**Solution:** Add `flush=True` to all print statements for immediate output in Docker containers:
```python
print(f"[worker] message received", flush=True)
```

### JSON Parse Errors
**Issue:** `Expecting property name enclosed in double quotes`
**Cause:** Message body is not valid JSON format
**Solution:** Ensure messages are properly formatted JSON using `ConvertTo-Json -Compress` or `json.dumps()`

### Worker Not Processing Messages
1. Check worker logs: `aws logs tail /ecs/mortgage-worker-logs --follow`
2. Verify environment variables are set correctly
3. Check SQS queue has messages: `aws sqs get-queue-attributes`
4. Verify IAM task role has `sqs:ReceiveMessage`, `sqs:DeleteMessage` permissions

### API Service Not Receiving Requests
1. Verify ALB is healthy: `aws elbv2 describe-target-health`
2. Check API logs
3. Verify security groups allow traffic from ALB
4. Test with: `curl http://$ALB_DNS/health`

## Performance Considerations

- **SQS Batch Processing**: Worker processes up to 5 messages per poll
- **Long Polling**: 20-second wait reduces API calls
- **S3 Partitioning**: Data organized by date (`dt=YYYY-MM-DD`) for efficient queries
- **KMS Encryption**: Slight performance overhead but provides encryption at rest

## Security

- **Encryption at Rest**: S3 data encrypted with KMS CMK
- **Encryption in Transit**: HTTPS enforced on S3, TLS for SQS
- **VPC**: Services run in private subnets
- **IAM**: Least privilege task roles
- **Public Access**: S3 bucket blocks all public access
- **Key Rotation**: KMS CMK automatic rotation enabled

## Cost Optimization

- **Fargate**: Pay only for running tasks
- **SQS**: Standard queue (cheaper than FIFO)
- **S3**: Consider S3 Intelligent-Tiering for cost optimization
- **Cleanup**: CDK configured with `removal_policy=RemovalPolicy.DESTROY` for test environments

## Development

### Local API Testing
```bash
cd services/api
pip install -r requirements.txt
uvicorn main:app --reload
```

### Run Worker Locally
```bash
cd services/worker
pip install -r requirements.txt
export QUEUE_URL="your-queue-url"
export BUCKET="your-bucket-name"
python app.py
```

## License

Proprietary - Internal Use Only