from pydantic import BaseModel


class SQSRunMessage(BaseModel):
    run_id: str
