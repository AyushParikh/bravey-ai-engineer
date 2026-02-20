import json
import logging

from src.worker.orchestrator import run_pipeline

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> dict:
    """AWS Lambda SQS handler. Processes one record at a time (BatchSize=1)."""
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        run_id = body["run_id"]
        logger.info(f"Processing run {run_id}")

        try:
            run_pipeline(run_id)
        except Exception:
            logger.exception(f"Pipeline failed for run {run_id}")
            raise  # Let SQS retry

    return {"statusCode": 200}
