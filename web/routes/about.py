"""S12：「關於」頁（公開頁面；只有版本資訊，不含 File ID 等設定）。"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from web.utils.db import ReadOnlyDB, get_db
from web.services.query import get_app_title
from web.utils.templates import templates as _T

router = APIRouter(tags=["頁面"])


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request, db: ReadOnlyDB = Depends(get_db)):
    import version
    return _T.TemplateResponse(request, "about.html", {
        "page":      "about",
        "app_title": get_app_title(db),
        "version":   version,
    })
