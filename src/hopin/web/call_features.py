from typing import TYPE_CHECKING

from fastapi import APIRouter
from pydantic import BaseModel

from hopin.config.constants import CALL_FEATURES_PATH, READ_METHODS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hopin.signaling.features import CallFeature


class CallFeatureList(BaseModel):
    features: list[str]


def create_call_features_router(features: Sequence[CallFeature]) -> APIRouter:
    router = APIRouter()
    names = [feature.name for feature in features]

    @router.api_route(CALL_FEATURES_PATH, methods=READ_METHODS)
    def call_features() -> CallFeatureList:
        return CallFeatureList(features=names)

    return router
