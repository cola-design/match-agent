import os
import re
from datetime import date, datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pymongo import MongoClient
from pymongo.collection import Collection

MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DB = os.environ.get("MONGODB_DB", "match_agent")
MONGODB_API_KEY = os.environ.get("MONGODB_API_KEY", "")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is required")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
db = client[MONGODB_DB]
products: Collection = db["products"]
prices: Collection = db["prices"]

app = FastAPI(title="match-agent MongoDB API", version="1.0.0")


def authenticate(x_api_key: str | None = Header(default=None)):
    if MONGODB_API_KEY and x_api_key != MONGODB_API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")


def clean_doc(doc: dict[str, Any]) -> dict[str, Any]:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def current_price(doc: dict[str, Any], as_of: date) -> bool:
    if str(doc.get("status", "")).upper() != "ACTIVE":
        return False
    start = as_date(doc.get("valid_from"))
    end = as_date(doc.get("valid_to"))
    return bool(start and end and start <= as_of <= end)


@app.get("/health")
def health(_: None = Depends(authenticate)):
    client.admin.command("ping")
    return {"status": "ok", "database": MONGODB_DB}


@app.get("/v1/products/{material_code}")
def product_by_code(material_code: str, _: None = Depends(authenticate)):
    code = material_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="material_code is required")
    docs = products.find(
        {"material_code": code, "data_status": "ACTIVE", "source_status": {"$in": ["有效", "ACTIVE", "在售"]}},
        {"_id": 0},
    ).limit(2)
    return [clean_doc(x) for x in docs]


@app.get("/v1/prices/{material_code}")
def price_by_code(
    material_code: str,
    as_of: date | None = Query(default=None, description="报价日期，YYYY-MM-DD"),
    _: None = Depends(authenticate),
):
    day = as_of or date.today()
    docs = prices.find({"material_code": material_code.strip(), "status": "ACTIVE"}, {"_id": 0})
    return [clean_doc(x) for x in docs if current_price(x, day)]


@app.get("/v1/products/search")
def product_search(
    q: str = Query(min_length=1, max_length=240),
    limit: int = Query(default=30, ge=1, le=50),
    _: None = Depends(authenticate),
):
    # Keep only meaningful tokens; exact material-code requests should use /v1/products/{code}.
    terms = [x for x in re.split(r"[\\s,，;；/／|]+", q.strip()) if len(x) >= 2]
    if not terms:
        terms = [q.strip()]
    ors = []
    for term in terms[:12]:
        pattern = re.escape(term)
        for field in ("material_code", "description", "specification_text", "category.name", "category.middle", "category.leaf"):
            ors.append({field: {"$regex": pattern, "$options": "i"}})
    query = {"data_status": "ACTIVE", "source_status": {"$in": ["有效", "ACTIVE", "在售"]}, "$or": ors}
    docs = products.find(query, {"_id": 0}).limit(limit)
    return [clean_doc(x) for x in docs]
