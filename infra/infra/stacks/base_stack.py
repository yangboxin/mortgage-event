from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_sqs as sqs,
    aws_s3 as s3,
    aws_kms as kms,
)
from constructs import Construct
from aws_cdk import aws_ecr as ecr


class BaseStack(Stack):
    """
    Base infrastructure stack:
      - VPC (2 AZ)
      - ECS Cluster (Fargate)
      - SQS Queue + DLQ
      - S3 Bucket (SSE-KMS)
      - KMS CMK
      - IAM Task Roles
      - CloudWatch Logs
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------
        # VPC
        # -----------------------------
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=1,  
        )

        # -----------------------------
        # KMS Key
        # -----------------------------
        data_key = kms.Key(
            self,
            "DataKey",
            alias="alias/mortgage-pipeline-data",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # -----------------------------
        # S3 Bucket（SSE-KMS）
        # -----------------------------
        bucket = s3.Bucket(
            self,
            "DataBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=data_key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            auto_delete_objects=True,         
            removal_policy=RemovalPolicy.DESTROY,
        )

        # -----------------------------
        # SQS + DLQ
        # -----------------------------
        dlq = sqs.Queue(
            self,
            "PaymentsDlq",
            retention_period=Duration.days(14),
        )

        queue = sqs.Queue(
            self,
            "PaymentsQueue",
            visibility_timeout=Duration.seconds(60),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=5,
                queue=dlq,
            ),
        )

        # -----------------------------
        # ECR Repository (worker)   
        # -----------------------------
        worker_repo = ecr.Repository.from_repository_name(
            self,
            "WorkerRepo",
            repository_name="mortgage-worker",
        )

        # -----------------------------
        # ECS Cluster
        # -----------------------------
        cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=vpc,
        )

        # -----------------------------
        # CloudWatch Logs
        # -----------------------------
        log_group = logs.LogGroup(
            self,
            "ServiceLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # -----------------------------
        # IAM Task Roles
        # -----------------------------

        # API Service Role
        api_task_role = iam.Role(
            self,
            "ApiTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Task role for API service",
        )
        api_task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sqs:SendMessage"],
                resources=[queue.queue_arn],
            )
        )

        # Publisher Role（outbox）
        publisher_task_role = iam.Role(
            self,
            "PublisherTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Task role for outbox publisher",
        )
        publisher_task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sqs:SendMessage"],
                resources=[queue.queue_arn],
            )
        )

        # Worker Role
        worker_task_role = iam.Role(
            self,
            "WorkerTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Task role for worker service",
        )
        worker_task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:ChangeMessageVisibility",
                    "sqs:GetQueueAttributes",
                ],
                resources=[queue.queue_arn],
            )
        )
        worker_task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject"],
                resources=[bucket.arn_for_objects("raw/*")],
            )
        )
        worker_task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "kms:Encrypt",
                    "kms:Decrypt",
                    "kms:GenerateDataKey",
                ],
                resources=[data_key.key_arn],
            )
        )

        # -----------------------------
        # Fargate Task Definitions
        # -----------------------------
        def make_task_def(name: str, role: iam.IRole) -> ecs.FargateTaskDefinition:
            task_def = ecs.FargateTaskDefinition(
                self,
                f"{name}TaskDef",
                cpu=256,
                memory_limit_mib=512,
                task_role=role,
            )
            task_def.add_container(
                f"{name}Container",
                image=ecs.ContainerImage.from_registry(
                    "public.ecr.aws/docker/library/amazonlinux:2023"
                ),
                command=["/bin/sh", "-c", "echo booted && sleep 3600"],
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix=name.lower(),
                    log_group=log_group,
                ),
            )
            return task_def

        worker_task_def = ecs.FargateTaskDefinition(
            self,
            "WorkerTaskDef",
            cpu=256,
            memory_limit_mib=512,
            task_role=worker_task_role,
        )

        worker_task_def.add_container(
            "WorkerContainer",
            image=ecs.ContainerImage.from_ecr_repository(worker_repo, tag="latest"),
            environment={
                "QUEUE_URL": queue.queue_url,
                "BUCKET": bucket.bucket_name,
                "AWS_REGION": self.region,
                "PREFIX": "raw",
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix="worker", log_group=log_group),
        )

        worker_task_def.execution_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")
        )

        # -----------------------------
        # Security Group
        # -----------------------------
        service_sg = ec2.SecurityGroup(
            self,
            "ServiceSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
        )

        # -----------------------------
        # ECS Service
        # -----------------------------
        ecs.FargateService(
            self,
            "WorkerService",
            cluster=cluster,
            task_definition=worker_task_def,
            desired_count=1,
            assign_public_ip=False,
            security_groups=[service_sg],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )
        CfnOutput(self, "PaymentsQueueUrl", value=queue.queue_url)
        CfnOutput(self, "DataBucketName", value=bucket.bucket_name)

