import json
import logging
import traceback

# Configure logging for Lambda — must set root logger level
logging.basicConfig(level=logging.INFO, force=True)
logger = logging.getLogger(__name__)

from src.worker.orchestrator import run_pipeline


def handler(event: dict, context) -> dict:
    """AWS Lambda SQS handler. Processes one record at a time (BatchSize=1)."""
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        run_id = body["run_id"]
        print(f"[BRAVEY] Processing run {run_id}", flush=True)

        try:
            run_pipeline(run_id)
        except Exception:
            print(f"[BRAVEY] Pipeline failed for run {run_id}:", flush=True)
            traceback.print_exc()
            raise  # Let SQS retry

    return {"statusCode": 200}
