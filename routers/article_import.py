from fastapi import APIRouter
from services.article_import_service import import_articles_from_source

router = APIRouter(prefix="/api/import-articles")


@router.post("/{source_id}")
def import_articles(source_id: int):
    result = import_articles_from_source(source_id)
    return result