from fastapi import FastAPI
from mangum import Mangum

from src.api.routes.health import router as health_router
from src.api.routes.webhooks import router as webhooks_router

app = FastAPI(title="Bravey Engineer Backend", version="0.1.0")

app.include_router(health_router)
app.include_router(webhooks_router)

# AWS Lambda handler via Mangum
handler = Mangum(app, lifespan="off")
