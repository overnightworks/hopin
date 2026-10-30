from typing import TYPE_CHECKING

from fastapi import APIRouter
from pydantic import BaseModel

from hopin.config.constants import CALL_FEATURES_PATH
from hopin.web.routes import READ_METHODS

if TYPE_CHECKING:
    from hopin.signaling.features import CallFeature


class CallFeatureList(BaseModel):
    features: list[str]


def create_call_features_router(feature: CallFeature | None) -> APIRouter:
    router = APIRouter()
    names = [] if feature is None else [feature.name]

    @router.api_route(CALL_FEATURES_PATH, methods=READ_METHODS)
    def call_features() -> CallFeatureList:
        return CallFeatureList(features=names)

    return router
