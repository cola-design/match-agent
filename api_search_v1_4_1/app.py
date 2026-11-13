"""Read-only MongoDB API; run independently of Dify's Python sandbox."""
import hmac
import logging
import os
import re
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Any
import unicodedata

if __package__:
    from .product_contract import ProductContractError, normalize_product
else:
    from product_contract import ProductContractError, normalize_product

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from dotenv import load_dotenv

# Each service owns its own local config file; explicit process env wins.
load_dotenv(Path(__file__).with_name('.env'), override=False, encoding='utf-8-sig')

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
API_VERSION = "1.4.1"
app = FastAPI(title="match-agent MongoDB API", version=API_VERSION)


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


def product_output(doc: dict[str, Any]) -> dict[str, Any]:
    result = clean_doc(normalize_product(doc))
    # Catalog ownership was confirmed by the user. This is a display default,
    # not a migration or permission to overwrite an explicitly maintained value.
    if not result.get('manufacturer'):
        result['manufacturer'] = '天正电气'
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


def visible_product_filter() -> dict[str, Any]:
    """Expose source-valid catalog entries without changing their review states."""
    return {
        "source_status": "有效",
        "data_status": {"$in": ["ACTIVE", "PENDING_REVIEW"]},
    }


# Directly searchable text fields. Dynamic standard_specs keys are searched separately
# with $objectToArray because MongoDB cannot regex-match every arbitrary object value
# through a single dotted field path.
SEARCH_TEXT_FIELDS = (
    "material_code", "description", "specification_text", "manufacturer",
    "brand", "model", "item_id", "category.name", "category.middle",
    "category.leaf", "source_attributes.name", "source_attributes.key",
    "source_attributes.value", "source_attributes.value_text",
    "source_attributes.attribute_name", "source_attributes.attribute_value",
)


def _standard_specs_match(pattern: str) -> dict[str, Any]:
    """Match a token against dynamic standard_specs keys and value fields."""
    spec_value = {
        "$cond": [
            {"$eq": [{"$type": "$$spec.v"}, "object"]},
            "$$spec.v.value_text",
            "$$spec.v",
        ]
    }
    searchable_value = {
        "$concat": [
            {"$ifNull": ["$$spec.k", ""]}, " ",
            {"$convert": {"input": spec_value, "to": "string", "onError": "", "onNull": ""}}, " ",
            {"$convert": {"input": {"$cond": [
                {"$eq": [{"$type": "$$spec.v"}, "object"]},
                "$$spec.v.value_num", None]}, "to": "string", "onError": "", "onNull": ""}}, " ",
            {"$convert": {"input": {"$cond": [
                {"$eq": [{"$type": "$$spec.v"}, "object"]},
                "$$spec.v.unit", None]}, "to": "string", "onError": "", "onNull": ""}},
        ]
    }
    return {
        "$expr": {
            "$anyElementTrue": [{
                "$map": {
                    "input": {"$objectToArray": {"$cond": [
                        {"$eq": [{"$type": "$standard_specs"}, "object"]},
                        "$standard_specs", {}
                    ]}},
                    "as": "spec",
                    "in": {"$regexMatch": {
                        "input": searchable_value,
                        "regex": pattern,
                        "options": "i",
                    }},
                }
            }]
        }
    }


def _term_match(term: str, *, model: bool = False) -> dict[str, Any]:
    pattern = model_pattern(term) if model else re.escape(term)
    fields = ("description", "specification_text", "manufacturer", "brand", "model",
              "item_id", "source_attributes.name", "source_attributes.key",
              "source_attributes.value", "source_attributes.value_text",
              "source_attributes.attribute_name", "source_attributes.attribute_value")
    if not model:
        fields = ("material_code",) + fields + ("category.name", "category.middle", "category.leaf")
    clauses = [{field: {"$regex": pattern, "$options": "i"}} for field in fields]
    clauses.append(_standard_specs_match(pattern))
    return {"$or": clauses}


def _normalize_product_rows(docs):
    """Normalize independently so one malformed product cannot fail a whole search page."""
    valid = []
    invalid = 0
    for doc in docs:
        try:
            valid.append(product_output(doc))
        except ProductContractError as exc:
            invalid += 1
            logger.warning("Skipping invalid product document: material_code=%s field=%s",
                           doc.get("material_code", "<missing>"), exc.field)
    return valid, invalid


@app.get("/live")
def liveness():
    """Process liveness only. /health additionally checks MongoDB authentication."""
    return {"status": "up"}


@app.get("/health", dependencies=[Depends(authenticate)])
def health():
    db.command("ping")
    return {"status": "ok", "database": MONGODB_DB}


@app.get("/ready", dependencies=[Depends(authenticate)])
def readiness():
    """Check actual collection read access, rather than process liveness alone."""
    db.command('ping')
    products.find_one({}, {'_id': 1}, max_time_ms=3000)
    prices.find_one({}, {'_id': 1}, max_time_ms=3000)
    return {'status': 'ready', 'database': MONGODB_DB, 'api_version': API_VERSION}


@app.get("/v1/status", dependencies=[Depends(authenticate)])
def service_status():
    rows = products.aggregate([{'$group': {
        '_id': {'data_status': '$data_status', 'source_status': '$source_status'},
        'count': {'$sum': 1},
    }}], maxTimeMS=5000)
    counts = {'total': 0, 'available': 0, 'pending_review': 0, 'other': 0}
    for row in rows:
        amount = row['count']; state = row['_id']
        counts['total'] += amount
        if state.get('data_status') == 'ACTIVE' and state.get('source_status') == '有效':
            counts['available'] += amount
        elif state.get('data_status') == 'PENDING_REVIEW' and state.get('source_status') == '有效':
            counts['pending_review'] += amount
        else:
            counts['other'] += amount
    return {'api_version': API_VERSION, 'database': MONGODB_DB,
            'checked_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
            'manufacturer': '天正电气', 'products': counts,
            'automatic_price_updates': False}


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
    limit: int = Query(default=200, ge=1, le=500),
    model: str = Query(default="", max_length=180),
    category: str = Query(default="", max_length=180),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=500),
):
    """Search products. Legacy callers still receive a list; page/page_size callers get metadata."""
    explicit_model = model.strip() or search_model(q)
    terms = list(dict.fromkeys(
        x for x in re.split(r"[\s,，;；/／|]+", q.strip()) if len(x) >= 2
    ))[:12]
    if not terms and not explicit_model:
        return []

    query = visible_product_filter()
    constraints = []
    if explicit_model:
        constraints.append(_term_match(explicit_model, model=True))
    if category.strip():
        required = category.strip()
        required = next((canonical for aliases, canonical in CATEGORY_ALIASES
                         if any(alias in required for alias in aliases)), required)
        fields = ("category.middle", "category.name", "category.leaf")
        constraints.append({"$or": [{field: {"$regex": re.escape(required), "$options": "i"}}
                                    for field in fields]})
    if not explicit_model and terms:
        constraints.append({"$or": [
            clause for term in terms for clause in _term_match(term)["$or"]
        ]})
    query["$and"] = constraints

    # Rank text hits before applying the page window. Keep scoring fields scalar and common.
    score = []
    for term in terms:
        text_checks = []
        for field in ("description", "specification_text", "manufacturer", "brand", "model"):
            # Guard non-string BSON values before applying $toLower/$indexOfCP.
            string_value = {
                "$cond": [
                    {"$eq": [{"$type": f"${field}"}, "string"]},
                    {"$toLower": f"${field}"},
                    "",
                ]
            }
            text_checks.append({"$gte": [{"$indexOfCP": [string_value, term.lower()]}, 0]})
        score.append({"$cond": [{"$or": text_checks}, 1, 0]})
    score_expr = {"$add": score} if score else 0

    # Backward compatibility: without page/page_size, return the original list shape
    # and retain the limit parameter (default 200, maximum 500).
    paginated = page is not None or page_size is not None
    if not paginated:
        pipeline = [
            {"$match": query},
            {"$addFields": {"__match_score": score_expr}},
            {"$sort": {"__match_score": -1, "material_code": 1, "_id": 1}},
            {"$limit": limit},
            {"$project": {"_id": 0, "__match_score": 0}},
        ]
        docs = list(products.aggregate(pipeline, maxTimeMS=5000, allowDiskUse=True))
        valid, invalid = _normalize_product_rows(docs)
        if invalid:
            logger.warning("Product search skipped %d invalid document(s)", invalid)
        return valid

    current_page = page or 1
    current_size = page_size or limit
    offset = (current_page - 1) * current_size
    # Count and fetch are separate operations so the page sort can spill to disk
    # instead of placing the entire result set inside a $facet memory limit.
    total = products.count_documents(query, maxTimeMS=5000)
    pipeline = [
        {"$match": query},
        {"$addFields": {"__match_score": score_expr}},
        {"$sort": {"__match_score": -1, "material_code": 1, "_id": 1}},
        {"$skip": offset},
        {"$limit": current_size},
        {"$project": {"_id": 0, "__match_score": 0}},
    ]
    raw_docs = list(products.aggregate(pipeline, maxTimeMS=8000, allowDiskUse=True))
    valid, invalid = _normalize_product_rows(raw_docs)
    return {
        "total": total,
        "page": current_page,
        "page_size": current_size,
        "returned": len(valid),
        "invalid_documents": invalid,
        "has_more": offset + len(raw_docs) < total,
        "data": valid,
    }


@app.get("/v1/products/{material_code}", dependencies=[Depends(authenticate)])
def product_by_code(material_code: str):
    code = material_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="material_code is required")
    docs = products.find(
        {**visible_product_filter(), "material_code": code}, {"_id": 0}
    ).limit(2).max_time_ms(3000)
    rows = list(docs)
    if len(rows) > 1:
        raise HTTPException(status_code=409, detail='duplicate eligible material code; database review required')
    if not rows:
        return []
    try:
        return [product_output(rows[0])]
    except ProductContractError as exc:
        logger.warning("Invalid product detail: material_code=%s field=%s", code, exc.field)
        raise HTTPException(status_code=422, detail={
            "message": "product document contract invalid",
            "field": exc.field,
        }) from None


@app.get("/v1/prices/{material_code}", dependencies=[Depends(authenticate)])
def price_by_code(
    material_code: str,
    as_of: date | None = Query(default=None, description="报价日期，YYYY-MM-DD；缺省按北京时间"),
):
    day = as_of or datetime.now(timezone(timedelta(hours=8))).date()
    day_text = day.isoformat()

    def mongo_day(field: str):
        return {"$dateToString": {
            "format": "%Y-%m-%d",
            "date": {"$convert": {
                "input": f"${field}", "to": "date", "onError": None, "onNull": None
            }},
            "timezone": "UTC",
        }}

    pipeline = [
        {"$match": {"material_code": material_code.strip(), "status": "ACTIVE"}},
        {"$addFields": {
            "__valid_from_day": mongo_day("valid_from"),
            "__valid_to_day": mongo_day("valid_to"),
        }},
        {"$match": {"$expr": {"$and": [
            {"$ne": ["$__valid_from_day", None]},
            {"$ne": ["$__valid_to_day", None]},
            {"$lte": ["$__valid_from_day", day_text]},
            {"$gte": ["$__valid_to_day", day_text]},
        ]}}},
        {"$project": {"_id": 0, "__valid_from_day": 0, "__valid_to_day": 0}},
        {"$limit": 101},
    ]
    docs = list(prices.aggregate(pipeline, maxTimeMS=3000))
    if len(docs) > 100:
        raise HTTPException(status_code=409, detail="too many effective price records for this material")
    return [clean_doc(doc) for doc in docs]

