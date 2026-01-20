# ================================
# ECR setup + build + push (Windows / PowerShell)
# ================================

$Region = "us-east-1"

$AccountId = (aws sts get-caller-identity --query Account --output text)

if (-not $AccountId) {
    Write-Error "Failed to get AWS Account ID. Check aws credentials."
    exit 1
}

Write-Host "Using AWS Account: $AccountId"
Write-Host "Region: $Region"

# Repositories to create
$Repos = @(
    "mortgage-processing",
    "mortgage-inference"
)

# -------------------------------
# 1) Create ECR repositories if not exist
# -------------------------------
foreach ($Repo in $Repos) {
    Write-Host "Checking ECR repo: $Repo"

    $exists = aws ecr describe-repositories `
        --repository-names $Repo `
        --region $Region 2>$null

    if (-not $exists) {
        Write-Host "Creating ECR repo: $Repo"
        aws ecr create-repository `
            --repository-name $Repo `
            --region $Region `
            --image-scanning-configuration scanOnPush=true `
            --encryption-configuration encryptionType=AES256 | Out-Null
    }
    else {
        Write-Host "ECR repo already exists: $Repo"
    }
}

# -------------------------------
# 2) Docker login to ECR (PowerShell-safe)
# -------------------------------
Write-Host "Logging into ECR..."

$LoginPassword = aws ecr get-login-password --region $Region

if (-not $LoginPassword) {
    Write-Error "Failed to get ECR login password."
    exit 1
}

docker login `
    --username AWS `
    --password $LoginPassword `
    "$AccountId.dkr.ecr.$Region.amazonaws.com"

if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker login failed."
    exit 1
}

# -------------------------------
# 3) Build + tag + push mortgage-processing
# -------------------------------
Write-Host "Building mortgage-processing image..."

Set-Location "ml\processing"

docker build -t mortgage-processing:latest .

docker tag mortgage-processing:latest `
    "$AccountId.dkr.ecr.$Region.amazonaws.com/mortgage-processing:latest"

docker push `
    "$AccountId.dkr.ecr.$Region.amazonaws.com/mortgage-processing:latest"

Set-Location "..\.."

# -------------------------------
# 4) Build + tag + push mortgage-inference
# -------------------------------
Write-Host "Building mortgage-inference image..."

Set-Location "ml\inference"

docker build -t mortgage-inference:latest .

docker tag mortgage-inference:latest `
    "$AccountId.dkr.ecr.$Region.amazonaws.com/mortgage-inference:latest"

docker push `
    "$AccountId.dkr.ecr.$Region.amazonaws.com/mortgage-inference:latest"

Set-Location "..\.."

Write-Host "All done."
Write-Host "Repos pushed:"
foreach ($Repo in $Repos) {
    Write-Host " - $AccountId.dkr.ecr.$Region.amazonaws.com/$Repo:latest"
}
