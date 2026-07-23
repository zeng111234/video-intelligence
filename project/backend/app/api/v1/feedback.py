from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_feedback_service

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


class FeedbackCreateRequest(BaseModel):
    publish_task_id: str = Field(..., min_length=1)
    views: int = Field(..., ge=0)
    likes: int = Field(0, ge=0)
    comments: int = Field(0, ge=0)
    leads: int = Field(0, ge=0)
    recorded_by: str = Field(..., min_length=1, max_length=80)
    note: str = Field("", max_length=500)


@router.get("")
def list_feedback(service=Depends(get_feedback_service)):
    return {"items": [item.model_dump(mode="json") for item in service.list_feedback()]}


@router.post("", status_code=201)
def create_feedback(body: FeedbackCreateRequest, service=Depends(get_feedback_service)):
    try:
        return service.record(**body.model_dump()).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/recommendations")
def feedback_recommendations(service=Depends(get_feedback_service)):
    return service.recommendations()
