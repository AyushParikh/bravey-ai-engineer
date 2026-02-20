import json

import boto3

from src.config import settings


def get_sqs_client():
    return boto3.client("sqs", region_name=settings.aws_region)


def send_run_message(run_id: str) -> dict:
    client = get_sqs_client()
    return client.send_message(
        QueueUrl=settings.sqs_queue_url,
        MessageBody=json.dumps({"run_id": str(run_id)}),
    )
