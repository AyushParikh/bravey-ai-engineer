from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db
from src.models.feature_flag import FeatureFlag

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok"}


@router.get("/feature-flags")
async def get_feature_flags(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FeatureFlag))
    flags = result.scalars().all()
    return {flag.name: flag.enabled for flag in flags}
