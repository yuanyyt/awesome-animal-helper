"""Animal story Wiki endpoints."""

from fastapi import APIRouter, HTTPException, Query

from src.backend.api.dependencies import get_wiki_repository
from src.backend.domain.models import WikiIndexResponse, WikiPageResponse

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


@router.get("", response_model=WikiIndexResponse)
async def list_wiki(
    q: str | None = Query(default=None, max_length=100),
    site: str | None = Query(default=None, max_length=50),
) -> WikiIndexResponse:
    """Browse generated pages by venue, scientific name, or content."""

    return get_wiki_repository().query(q=q, site=site)


@router.get("/page", response_model=WikiPageResponse)
async def get_wiki_page(
    site: str = Query(max_length=50),
    scientific_name: str = Query(max_length=120),
    animal: str = Query(max_length=50),
) -> WikiPageResponse:
    """Read one exact animal page without accepting filesystem paths."""

    try:
        return get_wiki_repository().get_page(
            site=site,
            scientific_name=scientific_name,
            animal=animal,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到这篇动物 Wiki") from exc
