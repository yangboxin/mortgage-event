# build-and-deploy.ps1
$AWS_REGION = "us-east-1"
$AWS_ACCOUNT = aws sts get-caller-identity --query Account --output text

echo "AWS Account: $AWS_ACCOUNT"
echo "Region: $AWS_REGION"
echo ""

# ECR Login
echo "[1/6] Logging into ECR..."
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"

# Build API
echo "[2/6] Building API image..."
cd services/api
docker build -t mortgage-api:latest .
docker tag mortgage-api:latest "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-api:latest"
docker push "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-api:latest"

# Build Worker
echo "[3/6] Building Worker image..."
cd ../worker
docker build -t mortgage-worker:latest .
docker tag mortgage-worker:latest "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-worker:latest"
docker push "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-worker:latest"

# Build Publisher
echo "[4/6] Building Publisher image..."
cd ../publisher
docker build -t mortgage-publisher:latest .
docker tag mortgage-publisher:latest "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-publisher:latest"
docker push "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/mortgage-publisher:latest"

# Deploy
echo "[5/6] Deploying infrastructure..."
cd ../../infra
cdk deploy

echo "[6/6] Done! ✅"