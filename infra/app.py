#!/usr/bin/env python3
import aws_cdk as cdk

from infra.stacks.base_stack import BaseStack

app = cdk.App()

BaseStack(
    app,
    "MortgagePipelineBaseStack",
)

app.synth()
