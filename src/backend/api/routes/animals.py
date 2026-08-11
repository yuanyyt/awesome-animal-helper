"""Animal catalogue endpoints."""

from fastapi import APIRouter, Query

from src.backend.api.dependencies import get_repository, get_wiki_repository
from src.backend.domain.models import AnimalListResponse

router = APIRouter(prefix="/api/animals", tags=["animals"])


@router.get("", response_model=AnimalListResponse)
async def list_animals(
    q: str | None = Query(default=None, max_length=100),
    site: str | None = Query(default=None, max_length=50),
    name: str | None = Query(default=None, max_length=50),
) -> AnimalListResponse:
    """List, search, or locate animals through one stable endpoint."""

    response = get_repository().query(q=q, site=site, name=name)
    wiki_counts = get_wiki_repository().fact_counts()
    return response.model_copy(
        update={
            "items": [
                animal.model_copy(update={"wiki_fact_count": wiki_counts.get(animal.name, 0)})
                for animal in response.items
            ]
        }
    )
