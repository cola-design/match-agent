"""Read-only MongoDB API; run independently of Dify's Python sandbox."""
import hmac
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
import unicodedata

from product_contract import ProductContractError, normalize_product

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
app = FastAPI(title="match-agent MongoDB API", version="1.2.0")


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


@app.exception_handler(ProductContractError)
async def product_contract_error_handler(request, exc):
    logger.error("Product contract violation in field: %s", exc.field)
    return JSONResponse(status_code=503, content={"detail": "product document contract invalid", "field": exc.field})


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


# Category aliases narrow candidate retrieval only; engineering validation remains in YAML.
CATEGORY_ALIASES = (
    (("小型漏电断路器", "微型漏电断路器"), "小型漏电断路器"),
    (("小型断路器", "微型断路器"), "小型断路器"),
    (("塑壳断路器",), "塑壳断路器"),
    (("漏电断路器",), "漏电断路器"),
    (("SPD后备保护器", "防雷后备保护器", "浪涌后备保护器"), "SPD后备保护器"),
    (("电涌保护器", "浪涌保护器"), "电涌保护器"),
    (("小型隔离开关", "交流隔离开关", "隔离开关"), "隔离开关"),
    (("成套柜体", "柜体箱体", "柜体"), "柜体"),
)


def search_model(q):
    # Compatibility for older clients. New workflows send an explicit model parameter.
    token = re.split(r"[\s;；,，]+", q.strip())[0]
    if re.fullmatch(r"[BCD]\d+(?:A)?|(?:AC|DC)\d+V|IP\d+", token, re.I):
        return ""
    return token if re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z][A-Za-z0-9._/-]{2,}", token) else ""


def model_pattern(model):
    normalized = unicodedata.normalize("NFKC", model.strip())
    pattern = re.escape(normalized)
    pattern = pattern.replace(r"\ ", r"\s*")
    pattern = re.sub(r"(?<=\d)[×xX](?=\d)", "[×xX*]", pattern)
    pattern = pattern.replace("mm2", "mm(?:2|²)")
    return r"(?<![A-Za-z0-9])" + pattern + r"(?![A-Za-z0-9])"


# Static /search must precede /{material_code}.
@app.get("/v1/products/search", dependencies=[Depends(authenticate)])
def product_search(
    q: str = Query(min_length=1, max_length=240),
    limit: int = Query(default=30, ge=1, le=50),
    model: str = Query(default="", max_length=180),
    category: str = Query(default="", max_length=180),
):
    explicit_model = model.strip() or search_model(q)
    terms = list(dict.fromkeys(x for x in re.split(r"[\s,，;；/／|]+", q.strip()) if len(x) >= 2))[:12]
    if not terms:
        return []
    query = active_product_filter()
    constraints = []
    if explicit_model:
        # Specified models must match; do not fill the candidate window with unrelated 4P/100A products.
        constraints.append({"description": {"$regex": model_pattern(explicit_model), "$options": "i"}})
    if category.strip():
        required = category.strip()
        required = next((canonical for aliases, canonical in CATEGORY_ALIASES
                         if any(alias in required for alias in aliases)), required)
        fields = ("category.middle", "category.name", "category")
        constraints.append({"$or": [{field: {"$regex": re.escape(required), "$options": "i"}} for field in fields]})
    fields = ("material_code", "description", "specification_text", "category.name", "category.middle", "category.leaf", "category")
    if not explicit_model:
        constraints.append({"$or": [{field: {"$regex": re.escape(term), "$options": "i"}}
                                     for term in terms for field in fields]})
    query["$and"] = constraints
    # Rank BEFORE limit so relevant current/poles/specs cannot be crowded out by natural record order.
    score = [{"$cond": [{"$or": [
        {"$gte": [{"$indexOfCP": [{"$toLower": {"$ifNull": ["$description", ""]}}, term.lower()]}, 0]},
        {"$gte": [{"$indexOfCP": [{"$toLower": {"$ifNull": ["$specification_text", ""]}}, term.lower()]}, 0]}
    ]}, 1, 0]} for term in terms]
    pipeline = [{"$match": query}, {"$addFields": {"__match_score": {"$add": score}}},
                {"$sort": {"__match_score": -1, "material_code": 1}}, {"$limit": limit},
                {"$project": {"_id": 0, "__match_score": 0}}]
    docs = products.aggregate(pipeline, maxTimeMS=5000)
    return [clean_doc(normalize_product(doc)) for doc in docs]


@app.get("/v1/products/{material_code}", dependencies=[Depends(authenticate)])
def product_by_code(material_code: str):
    code = material_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="material_code is required")
    docs = products.find(
        {**active_product_filter(), "material_code": code}, {"_id": 0}
    ).limit(2).max_time_ms(3000)
    return [clean_doc(normalize_product(doc)) for doc in docs]


@app.get("/v1/prices/{material_code}", dependencies=[Depends(authenticate)])
def price_by_code(
    material_code: str,
    as_of: date | None = Query(default=None, description="报价日期，YYYY-MM-DD；缺省按北京时间"),
):
    day = as_of or datetime.now(timezone(timedelta(hours=8))).date()
    docs = list(prices.find(
        {"material_code": material_code.strip(), "status": "ACTIVE"}, {"_id": 0}
    ).limit(101).max_time_ms(3000))
    if len(docs) > 100:
        raise HTTPException(status_code=409, detail="too many price versions for this material")
    # Accept both imported BSON dates and ISO date strings. Preserve overlapping records
    # so the workflow can block ambiguous pricing instead of choosing arbitrarily.
    return [clean_doc(doc) for doc in docs if current_price(doc, day)]
