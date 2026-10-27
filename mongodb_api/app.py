"""Read-only MongoDB API; run independently of Dify's Python sandbox."""
import hmac
import logging
import os
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pymongo import MongoClient
from pymongo.errors import PyMongoError

MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DB = os.environ.get("MONGODB_DB", "match_agent")
MONGODB_API_KEY = os.environ.get("MONGODB_API_KEY", "")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is required")
if not MONGODB_API_KEY or MONGODB_API_KEY == "replace-with-a-long-random-key":
    raise RuntimeError("Set a private MONGODB_API_KEY before starting the service")

client = MongoClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=3000,
    connectTimeoutMS=3000,
    socketTimeoutMS=5000,
    timeoutMS=8000,
    maxPoolSize=20,
    appname="match-agent-api",
)
db = client[MONGODB_DB]
products = db["products"]
prices = db["prices"]
logger = logging.getLogger("match_agent_api")
app = FastAPI(title="match-agent MongoDB API", version="1.1.0")


def authenticate(x_api_key: str | None = Header(default=None)):
    if not x_api_key or not hmac.compare_digest(
        x_api_key.encode("utf-8"), MONGODB_API_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid api key")


@app.exception_handler(PyMongoError)
async def database_error_handler(request, exc):
    # Log only the class, not credentials, full URIs or request input.
    logger.error("MongoDB operation failed: %s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "database operation unavailable"})


def clean_doc(doc: dict[str, Any]) -> dict[str, Any]:
    result = dict(doc)
    result.pop("_id", None)
    return result


def as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def current_price(doc: dict[str, Any], as_of: date) -> bool:
    if str(doc.get("status", "")).upper() != "ACTIVE":
        return False
    start, end = as_date(doc.get("valid_from")), as_date(doc.get("valid_to"))
    return bool(start and end and start <= as_of <= end)


def active_product_filter() -> dict[str, Any]:
    return {"data_status": "ACTIVE", "source_status": "有效"}


@app.get("/live")
def liveness():
    """Process liveness only. /health additionally checks MongoDB authentication."""
    return {"status": "up"}


@app.get("/health", dependencies=[Depends(authenticate)])
def health():
    db.command("ping")
    return {"status": "ok", "database": MONGODB_DB}


# Static /search must be declared before the /{material_code} route.
@app.get("/v1/products/search", dependencies=[Depends(authenticate)])
def product_search(
    q: str = Query(min_length=1, max_length=240),
    limit: int = Query(default=30, ge=1, le=50),
):
    terms = [x for x in re.split(r"[\s,，;；/／|]+", q.strip()) if len(x) >= 2]
    if not terms:
        return []
    fields = ("material_code", "description", "specification_text",
              "category.name", "category.middle", "category.leaf")
    clauses = [
        {field: {"$regex": re.escape(term), "$options": "i"}}
        for term in terms[:12] for field in fields
    ]
    query = {**active_product_filter(), "$or": clauses}
    docs = products.find(query, {"_id": 0}).limit(limit).max_time_ms(3000)
    # These are retrieval candidates; the workflow must still validate all requirements.
    return [clean_doc(doc) for doc in docs]


@app.get("/v1/products/{material_code}", dependencies=[Depends(authenticate)])
def product_by_code(material_code: str):
    code = material_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="material_code is required")
    docs = products.find(
        {**active_product_filter(), "material_code": code}, {"_id": 0}
    ).limit(2).max_time_ms(3000)
    return [clean_doc(doc) for doc in docs]


@app.get("/v1/prices/{material_code}", dependencies=[Depends(authenticate)])
def price_by_code(
    material_code: str,
    as_of: date | None = Query(default=None, description="报价日期，YYYY-MM-DD；缺省按北京时间"),
):
    day = as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    docs = list(prices.find(
        {"material_code": material_code.strip(), "status": "ACTIVE"}, {"_id": 0}
    ).limit(101).max_time_ms(3000))
    if len(docs) > 100:
        raise HTTPException(status_code=409, detail="too many price versions for this material")
    # Accept both imported BSON dates and ISO date strings. Preserve overlapping records
    # so the workflow can block ambiguous pricing instead of choosing arbitrarily.
    return [clean_doc(doc) for doc in docs if current_price(doc, day)]
