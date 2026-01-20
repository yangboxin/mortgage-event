# analytics_stack.py
from __future__ import annotations

from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_kms as kms,
    aws_iam as iam,
    aws_ecr as ecr,
    aws_sagemaker as sagemaker,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct


class AnalyticsStack(Stack):
    """
    Analytics/ML stack (isolated from BaseStack):
      - SageMaker Processing (ECR image)
      - SageMaker Model (ECR image) for Batch Transform
      - Step Functions StateMachine: Processing -> Transform
      - (No schedules here; run manually first, add EventBridge later if needed)
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        bucket: s3.IBucket,
        data_key: kms.IKey,
        processing_repo_name: str = "mortgage-processing",
        inference_repo_name: str = "mortgage-inference",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------
        # ECR repos
        # -----------------------------
        processing_repo = ecr.Repository.from_repository_name(
            self, "ProcessingRepo", repository_name=processing_repo_name
        )
        inference_repo = ecr.Repository.from_repository_name(
            self, "InferenceRepo", repository_name=inference_repo_name
        )

        # -----------------------------
        # SageMaker Security Group (new)
        # -----------------------------
        sm_sg = ec2.SecurityGroup(
            self,
            "SageMakerSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
            description="Security group for SageMaker Processing/Transform in VPC",
        )

        sm_subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS).subnets
        sm_subnet_ids = [s.subnet_id for s in sm_subnets]
        sm_sg_ids = [sm_sg.security_group_id]

        # -----------------------------
        # SageMaker execution role (Processing + Transform)
        # -----------------------------
        sm_role = iam.Role(
            self,
            "SageMakerExecutionRole",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            description="Execution role for SageMaker Processing + Batch Transform",
        )

        # S3 read/write (SSE-KMS bucket) + KMS
        bucket.grant_read_write(sm_role)
        data_key.grant_encrypt_decrypt(sm_role)

        # -----------------------------
        # SageMaker Model (for Batch Transform)
        # -----------------------------
        sm_model = sagemaker.CfnModel(
            self,
            "MortgageBatchModel",
            execution_role_arn=sm_role.role_arn,
            primary_container=sagemaker.CfnModel.ContainerDefinitionProperty(
                image=inference_repo.repository_uri_for_tag("latest"),
                environment={"OUTPUT_FORMAT": "jsonl"},
            ),
            vpc_config=sagemaker.CfnModel.VpcConfigProperty(
                security_group_ids=sm_sg_ids,
                subnets=sm_subnet_ids,
            ),
        )

        # -----------------------------
        # Step Functions role (must PassRole to SageMaker)
        # -----------------------------
        sf_role = iam.Role(
            self,
            "MlPipelineStateMachineRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
            description="Role for Step Functions to orchestrate SageMaker jobs",
        )

        sf_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "sagemaker:CreateProcessingJob",
                    "sagemaker:DescribeProcessingJob",
                    "sagemaker:StopProcessingJob",
                    "sagemaker:CreateTransformJob",
                    "sagemaker:DescribeTransformJob",
                    "sagemaker:StopTransformJob",
                ],
                resources=["*"],
            )
        )
        sf_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[sm_role.role_arn],
            )
        )

        # -----------------------------
        # Step Functions definition
        # Input example: {"dt":"2026-01-18"}
        # -----------------------------
        processing_job_name = sfn.JsonPath.format(
            "mortgage-proc-{}", sfn.JsonPath.string_at("$.dt")
        )
        transform_job_name = sfn.JsonPath.format(
            "mortgage-xform-{}", sfn.JsonPath.string_at("$.dt")
        )

        raw_s3_prefix = sfn.JsonPath.format(
            f"s3://{bucket.bucket_name}/raw/payments/dt={{}}/",
            sfn.JsonPath.string_at("$.dt"),
        )
        curated_s3_prefix = sfn.JsonPath.format(
            f"s3://{bucket.bucket_name}/curated/payments/dt={{}}/",
            sfn.JsonPath.string_at("$.dt"),
        )
        pred_s3_prefix = sfn.JsonPath.format(
            f"s3://{bucket.bucket_name}/predictions/payments/dt={{}}/",
            sfn.JsonPath.string_at("$.dt"),
        )

        # -----------------------------
        # Processing: create + describe + wait loop
        # -----------------------------
        start_processing = tasks.CallAwsService(
            self,
            "StartProcessingJob",
            service="sagemaker",
            action="createProcessingJob",
            parameters={
                "ProcessingJobName": processing_job_name,
                "RoleArn": sm_role.role_arn,
                "AppSpecification": {
                    "ImageUri": processing_repo.repository_uri_for_tag("latest"),
                },
                "ProcessingResources": {
                    "ClusterConfig": {
                        "InstanceType": "ml.m5.large",
                        "InstanceCount": 1,
                        "VolumeSizeInGB": 30,
                    }
                },
                "NetworkConfig": {
                    "VpcConfig": {
                        "SecurityGroupIds": sm_sg_ids,
                        "Subnets": sm_subnet_ids,
                    }
                },
                "ProcessingInputs": [
                    {
                        "InputName": "raw",
                        "S3Input": {
                            "S3Uri": raw_s3_prefix,
                            "LocalPath": "/opt/ml/processing/input",
                            "S3DataType": "S3Prefix",
                            "S3InputMode": "File",
                        },
                    }
                ],
                "ProcessingOutputConfig": {
                    "Outputs": [
                        {
                            "OutputName": "curated",
                            "S3Output": {
                                "S3Uri": curated_s3_prefix,
                                "LocalPath": "/opt/ml/processing/output",
                                "S3UploadMode": "EndOfJob",
                            },
                        }
                    ]
                },
                "Environment": {"OUTPUT_FORMAT": "jsonl"},
            },
            iam_resources=["*"],
            result_path="$.processingCreate",
        )

        describe_processing = tasks.CallAwsService(
            self,
            "DescribeProcessingJob",
            service="sagemaker",
            action="describeProcessingJob",
            parameters={"ProcessingJobName": processing_job_name},
            iam_resources=["*"],
            result_path="$.processingDesc",
        )

        wait_processing = sfn.Wait(
            self,
            "WaitProcessing30s",
            time=sfn.WaitTime.duration(Duration.seconds(30)),
        )

        processing_done = sfn.Choice(self, "ProcessingDone?")
        processing_failed = sfn.Fail(
            self, "ProcessingFailed", cause="SageMaker Processing failed/stopped"
        )

        # IMPORTANT: only set next for describe_processing ONCE
        describe_processing.next(processing_done)

        # Poll step: wait -> describe (describe already goes to processing_done)
        processing_poll = wait_processing.next(describe_processing)

        # -----------------------------
        # Transform: create + describe + wait loop
        # -----------------------------
        start_transform = tasks.CallAwsService(
            self,
            "StartTransformJob",
            service="sagemaker",
            action="createTransformJob",
            parameters={
                "TransformJobName": transform_job_name,
                "ModelName": sm_model.attr_model_name,
                "TransformInput": {
                    "DataSource": {
                        "S3DataSource": {
                            "S3DataType": "S3Prefix",
                            "S3Uri": curated_s3_prefix,
                        }
                    },
                    "ContentType": "application/jsonlines",
                    "SplitType": "Line",
                },
                "TransformOutput": {
                    "S3OutputPath": pred_s3_prefix,
                    "AssembleWith": "Line",
                },
                "TransformResources": {
                    "InstanceType": "ml.m5.large",
                    "InstanceCount": 1,
                },
                "MaxConcurrentTransforms": 2,
                "MaxPayloadInMB": 6,
            },
            iam_resources=["*"],
            result_path="$.transformCreate",
        )
        # Ensure model exists before state machine runs jobs
        start_transform.node.add_dependency(sm_model)

        describe_transform = tasks.CallAwsService(
            self,
            "DescribeTransformJob",
            service="sagemaker",
            action="describeTransformJob",
            parameters={"TransformJobName": transform_job_name},
            iam_resources=["*"],
            result_path="$.transformDesc",
        )

        wait_transform = sfn.Wait(
            self,
            "WaitTransform30s",
            time=sfn.WaitTime.duration(Duration.seconds(30)),
        )

        transform_done = sfn.Choice(self, "TransformDone?")
        transform_failed = sfn.Fail(
            self, "TransformFailed", cause="SageMaker Transform failed/stopped"
        )
        pipeline_succeeded = sfn.Succeed(self, "PipelineSucceeded")

        # IMPORTANT: only set next for describe_transform ONCE
        describe_transform.next(transform_done)

        # Poll step: wait -> describe (describe already goes to transform_done)
        transform_poll = wait_transform.next(describe_transform)

        # -----------------------------
        # Wiring: loop until Completed / Failed / Stopped
        # -----------------------------
        processing_done.when(
            sfn.Condition.string_equals("$.processingDesc.ProcessingJobStatus", "Completed"),
            # Start transform, then describe (describe already goes to transform_done)
            start_transform.next(describe_transform),
        )
        processing_done.when(
            sfn.Condition.or_(
                sfn.Condition.string_equals("$.processingDesc.ProcessingJobStatus", "Failed"),
                sfn.Condition.string_equals("$.processingDesc.ProcessingJobStatus", "Stopped"),
            ),
            processing_failed,
        )
        processing_done.otherwise(processing_poll)

        transform_done.when(
            sfn.Condition.string_equals("$.transformDesc.TransformJobStatus", "Completed"),
            pipeline_succeeded,
        )
        transform_done.when(
            sfn.Condition.or_(
                sfn.Condition.string_equals("$.transformDesc.TransformJobStatus", "Failed"),
                sfn.Condition.string_equals("$.transformDesc.TransformJobStatus", "Stopped"),
            ),
            transform_failed,
        )
        transform_done.otherwise(transform_poll)

        # -----------------------------
        # State machine entry
        # -----------------------------
        definition = start_processing.next(describe_processing)

        state_machine = sfn.StateMachine(
            self,
            "MortgageMlPipeline",
            role=sf_role,
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.hours(2),
        )

        # -----------------------------
        # Outputs
        # -----------------------------
        CfnOutput(self, "MlStateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "SageMakerExecutionRoleArn", value=sm_role.role_arn)
        CfnOutput(self, "SageMakerModelName", value=sm_model.attr_model_name)
        CfnOutput(self, "SageMakerSecurityGroupId", value=sm_sg.security_group_id)
