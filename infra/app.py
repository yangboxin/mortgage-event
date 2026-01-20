#!/usr/bin/env python3
from aws_cdk import App, Environment
from infra.stacks.base_stack import BaseStack
from infra.stacks.analytics_stack import AnalyticsStack

app = App()
env = Environment(account="702630738474", region="us-east-1")

base = BaseStack(app, "MortgagePipelineBaseStack", env=env)

analytics = AnalyticsStack(
    app, "MortgageAnalyticsStack",
    env=env,
    vpc=base.vpc,
    bucket=base.bucket,
    data_key=base.data_key,
)

analytics.add_dependency(base)

app.synth()
