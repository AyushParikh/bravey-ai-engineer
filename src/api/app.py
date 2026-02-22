from fastapi import FastAPI
from mangum import Mangum

from src.api.routes.auth import router as auth_router
from src.api.routes.health import router as health_router
from src.api.routes.integrations import router as integrations_router
from src.api.routes.organizations import router as organizations_router
from src.api.routes.billing import router as billing_router
from src.api.routes.webhooks import router as webhooks_router

app = FastAPI(title="Bravey Engineer Backend", version="0.1.0")

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(organizations_router)
app.include_router(integrations_router)
app.include_router(billing_router)
app.include_router(webhooks_router)

# AWS Lambda handler via Mangum
handler = Mangum(app, lifespan="off")
