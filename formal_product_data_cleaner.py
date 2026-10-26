#!/usr/bin/env python3
"""Clean product-data.rar into candidate JSONL files."""
from __future__ import annotations

import ctypes as c
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

SOURCE = Path("/workspace/attachments/0a65b59d-e452-40db-9203-cb8b8c8b8122/product-data.rar")
OUT = Path("/workspace/project-review/cleaned_formal_api")
OUT.mkdir(parents=True, exist_ok=True)

lib = c.CDLL("/usr/lib/x86_64-linux-gnu/libarchive.so.13")
def bind(name, args, result):
    f = getattr(lib, name)
    f.argtypes, f.restype = args, result
    return f
new = bind("archive_read_new", [], c.c_void_p)
support_format = bind("archive_read_support_format_all", [c.c_void_p], c.c_int)
support_filter = bind("archive_read_support_filter_all", [c.c_void_p], c.c_int)
open_file = bind("archive_read_open_filename", [c.c_void_p, c.c_char_p, c.c_size_t], c.c_int)
next_header = bind("archive_read_next_header", [c.c_void_p, c.POINTER(c.c_void_p)], c.c_int)
pathname = bind("archive_entry_pathname", [c.c_void_p], c.c_char_p)
filetype = bind("archive_entry_filetype", [c.c_void_p], c.c_uint)
read_data = bind("archive_read_data", [c.c_void_p, c.c_void_p, c.c_size_t], c.c_ssize_t)
error_string = bind("archive_error_string", [c.c_void_p], c.c_char_p)
free_reader = bind("archive_read_free", [c.c_void_p], c.c_int)

def entries() -> Iterator[tuple[str, bytes]]:
    reader = new()
    support_format(reader)
    support_filter(reader)
    if open_file(reader, str(SOURCE).encode(), 10240) < 0:
        raise RuntimeError(error_string(reader))
    buffer = c.create_string_buffer(1024 * 1024)
    entry = c.c_void_p()
    try:
        while True:
            status = next_header(reader, c.byref(entry))
            if status == 1:
                return
            if status < 0:
                raise RuntimeError(error_string(reader))
            name = pathname(entry).decode("utf-8", "replace")
            if filetype(entry) == 0o040000:
                continue
            chunks = []
            while True:
                count = read_data(reader, buffer, len(buffer))
                if count == 0:
                    break
                if count < 0:
                    raise RuntimeError(f"{name}: {error_string(reader)}")
                chunks.append(buffer.raw[:count])
            yield name, b"".join(chunks)
    finally:
        free_reader(reader)

def repair(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return value.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value

def clean_text(value: Any) -> str:
    return repair("" if value is None else str(value)).strip()

def parse_page(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8-sig"))

ALIASES = {
    "型号说明": "model", "型号": "model",
    "壳架电流等级": "frame_current_a", "壳架电流": "frame_current_a", "壳架": "frame_current_a",
    "额定电流": "rated_current_a", "额定工作电流": "rated_current_a",
    "工作电流": "operating_current_a", "极数": "pole_count",
    "分断能力": "breaking_capacity", "额定极限短路分断能力": "breaking_capacity",
    "额定电压": "rated_voltage", "额定工作电压": "rated_voltage",
    "工作电压": "operating_voltage", "控制电压": "control_voltage",
    "额定频率": "frequency_hz", "频率": "frequency_hz",
    "脱扣方式": "trip_mechanism", "保护类型": "protection_type",
    "安装方式": "mounting_method", "附件代码": "accessory_code", "附件代号": "accessory_code",
    "分断": "breaking_capacity",
    "附件电压等级": "accessory_voltage", "控制器类别": "controller_type",
    "特殊说明": "special_note", "防护等级": "ip_rating",
    "额定短路接通能力": "short_circuit_make_capacity",
    "额定短路分断能力": "short_circuit_breaking_capacity",
}

def value_parts(value: str) -> dict[str, Any]:
    result: dict[str, Any] = {"value_text": value}
    if value in ("", "/"):
        result["is_empty"] = True
        return result
    match = re.search(r"(?<![A-Za-z])([-+]?\d+(?:\.\d+)?)\s*([A-Za-zΩμ%]+)?", value)
    if match:
        number = float(match.group(1))
        result["value_num"] = int(number) if number.is_integer() else number
        if match.group(2):
            result["unit"] = match.group(2)
    return result

def parse_spec_string(raw: str, issues: list[dict[str, Any]], code: str, source: str) -> list[dict[str, Any]]:
    attrs = []
    for index, fragment in enumerate(raw.split("@") if raw else []):
        fragment = fragment.strip()
        if not fragment:
            issues.append({"material_code": code, "issue": "EMPTY_SPEC_FRAGMENT", "source": source, "detail": raw})
            continue
        if "&" not in fragment:
            issues.append({"material_code": code, "issue": "MALFORMED_SPEC_FRAGMENT", "source": source, "detail": fragment})
            continue
        name, value = (clean_text(part) for part in fragment.split("&", 1))
        if not name:
            issues.append({"material_code": code, "issue": "EMPTY_SPEC_NAME", "source": source, "detail": fragment})
            continue
        attrs.append({"name": name, "value": value, "source": source, "position": index})
    return attrs

def add_standard_specs(attrs: list[dict[str, Any]], issues: list[dict[str, Any]], code: str) -> tuple[dict[str, Any], list[str]]:
    standard: dict[str, Any] = {}
    flags: list[str] = []
    for attr in attrs:
        key = ALIASES.get(attr["name"])
        if not key:
            continue
        item = value_parts(attr["value"])
        item["raw_name"] = attr["name"]
        item["source"] = attr.get("source", "")
        if key in standard:
            existing = standard[key] if isinstance(standard[key], list) else [standard[key]]
            # The same value reported by type1 and type2 is a confirmation,
            # not a duplicate. Preserve different values and flag them.
            if any(old.get("value_text") == item.get("value_text") for old in existing):
                continue
            if any(old.get("source") == item.get("source") for old in existing):
                flags.append("DUPLICATE_STANDARD_SPEC")
                issues.append({"material_code": code, "issue": "DUPLICATE_STANDARD_SPEC", "source": attr.get("source", ""), "detail": key})
            if not isinstance(standard[key], list):
                standard[key] = [standard[key]]
            standard[key].append(item)
        else:
            standard[key] = item
    return standard, sorted(set(flags))

def category(name: str, code: str) -> dict[str, Any]:
    # Only the first two dots delimit the documented 3-level category path.
    # Series names can themselves contain dots, e.g. TGM1N(祥云3.0).
    path = [part.strip() for part in name.split(".", 2) if part.strip()]
    result: dict[str, Any] = {"code": code, "name": name, "path": path}
    if path:
        result["root"] = path[0]
        result["middle"] = path[1] if len(path) > 1 else path[0]
        result["leaf"] = path[-1]
    return result

def jsonl(path: Path, rows: Iterator[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count

type1: dict[str, dict[str, Any]] = {}
type2: dict[str, list[dict[str, Any]]] = defaultdict(list)
type2_base: dict[str, dict[str, Any]] = {}
type2_invalid: Counter[str] = Counter()
issues: list[dict[str, Any]] = []
type2_audit_issues: list[dict[str, Any]] = []
page_counts = Counter()
row_counts = Counter()
status_counts = {"1": Counter(), "2": Counter()}

for name, body in entries():
    match = re.search(r"/raw/type([12])/page-(\d+)\.json$", name)
    if not match:
        continue
    kind = match.group(1)
    page = int(match.group(2))
    page_counts[kind] += 1
    payload = parse_page(body)
    if payload.get("status") is not True or not isinstance(payload.get("data"), list):
        issues.append({"material_code": "", "issue": "BAD_API_PAGE", "source": name, "detail": payload.get("message", "")})
        continue
    for raw in payload["data"]:
        row = {key: clean_text(value) for key, value in raw.items()}
        code = row.get("ITEM_NUM", "")
        if not code:
            issues.append({"material_code": "", "issue": "MISSING_MATERIAL_CODE", "source": name, "detail": row})
            continue
        source_status = row.get("STATUS", "")
        status_counts[kind][source_status] += 1
        row_counts[kind] += 1
        if kind == "1":
            attrs = parse_spec_string(row.get("SPEC_STRING", ""), issues, code, "type1")
            type1[code] = {"row": row, "attrs": attrs, "page": page, "source": name}
        elif source_status == "有效":
            attr_name = row.get("ATTR_NAME", "")
            attr_value = row.get("ATTR_VALUE", "")
            if not attr_name:
                issues.append({"material_code": code, "issue": "MISSING_ATTRIBUTE_NAME", "source": name, "detail": row})
            type2_base.setdefault(code, row)
            type2[code].append({"name": attr_name, "value": attr_value, "source": "type2", "attr_id": row.get("ATTR_ID", ""), "sa_id": row.get("SA_ID", "")})
        else:
            type2_invalid[code] += 1

def compare_attrs(code: str, attrs1: list[dict[str, Any]], attrs2: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    by_name1: dict[str, set[str]] = defaultdict(set)
    by_name2: dict[str, set[str]] = defaultdict(set)
    for attr in attrs1:
        by_name1[ALIASES.get(attr["name"], attr["name"])].add(attr["value"])
    for attr in attrs2:
        by_name2[ALIASES.get(attr["name"], attr["name"])].add(attr["value"])
    for name in set(by_name1) & set(by_name2):
        if by_name1[name] != by_name2[name]:
            flags.append("TYPE1_TYPE2_ATTRIBUTE_CONFLICT")
            type2_audit_issues.append({"material_code": code, "issue": "TYPE1_TYPE2_ATTRIBUTE_CONFLICT", "source": "type1/type2", "detail": name})
    if any(len(values) > 1 for values in by_name2.values()):
        flags.append("DUPLICATE_TYPE2_ATTRIBUTE")
        type2_audit_issues.append({"material_code": code, "issue": "DUPLICATE_TYPE2_ATTRIBUTE", "source": "type2", "detail": [name for name, values in by_name2.items() if len(values) > 1]})
    return sorted(set(flags))

def product_docs() -> Iterator[dict[str, Any]]:
    for code in sorted(type1):
        item = type1[code]
        row = item["row"]
        attrs1 = item["attrs"]
        attrs2 = type2.get(code, [])
        flags: list[str] = []
        if not row.get("ITEM_DESC"):
            flags.append("MISSING_DESCRIPTION")
        if not row.get("CATEGORY_NAME"):
            flags.append("MISSING_CATEGORY")
        if not row.get("SPEC_STRING"):
            flags.append("MISSING_SPECIFICATIONS")
        all_attrs = attrs1 + attrs2
        standard, standard_flags = add_standard_specs(all_attrs, issues, code)
        flags.extend(standard_flags)
        source_status = row.get("STATUS", "")
        # The upstream STATUS is a mutable snapshot (for example, an
        # incomplete product can later become valid). Do not turn it into a
        # permanent lifecycle state. Publication remains pending until review.
        data_status = "PENDING_REVIEW"
        if source_status not in ("有效", "无效"):
            flags.append("UNKNOWN_SOURCE_STATUS")
        yield {
            "material_code": code,
            "item_id": row.get("ITEM_ID", ""),
            "description": row.get("ITEM_DESC", ""),
            "category": category(row.get("CATEGORY_NAME", ""), row.get("CATEGORY_CODE", "")),
            "standard_specs": standard,
            "source_attributes": all_attrs,
            "specification_text": "；".join(f"{attr['name']}={attr['value']}" for attr in attrs1),
            "source_status": source_status,
            "data_status": data_status,
            "quality_flags": sorted(set(flags)),
            "last_update_date": row.get("LAST_UPDATE_DATE", ""),
            "provenance": {
                "source": "product-spec-api",
                "date_from": "2017-01-01",
                "type1_page": item["page"],
                "type2_attribute_count_valid": len(attrs2),
            },
        }

def type2_only_docs() -> Iterator[dict[str, Any]]:
    for code in sorted(set(type2) - set(type1)):
        attrs = type2[code]
        row = type2_base.get(code, {})
        yield {
            "material_code": code,
            "item_id": row.get("ITEM_ID", ""),
            "description": row.get("ITEM_DESC", ""),
            "category": category(row.get("CATEGORY_NAME", ""), row.get("CATEGORY_CODE", "")),
            "standard_specs": add_standard_specs(attrs, issues, code)[0],
            "source_attributes": attrs,
            "data_status": "PENDING_REVIEW",
            "quality_flags": ["TYPE1_PRODUCT_RECORD_MISSING"],
            "provenance": {"source": "product-spec-api", "date_from": "2017-01-01", "type2_only": True},
        }

today = date(2026, 10, 25)
products_path = OUT / "products_candidates.jsonl"
products_pending_path = OUT / "products_pending_review.jsonl"
products_source_invalid_path = OUT / "products_source_invalid_current.jsonl"
products_auto_active_path = OUT / "products_auto_active_candidates.jsonl"
type2_only_path = OUT / "type2_only_candidates.jsonl"
issues_path = OUT / "issues.jsonl"
type2_audit_issues_path = OUT / "type2_audit_issues.jsonl"
prices_path = OUT / "prices_candidates.jsonl"
product_count = jsonl(products_path, product_docs())
pending_count = 0
source_invalid_count = 0
auto_active_count = 0
with products_path.open("r", encoding="utf-8") as source, \
        products_pending_path.open("w", encoding="utf-8") as pending, \
        products_source_invalid_path.open("w", encoding="utf-8") as source_invalid, \
        products_auto_active_path.open("w", encoding="utf-8") as auto_active:
    for line in source:
        record = json.loads(line)
        pending.write(line)
        pending_count += 1
        if record.get("source_status") == "无效":
            source_invalid.write(line)
            source_invalid_count += 1
        flags = set(record.get("quality_flags", []))
        auto_ok = (
            record.get("source_status") == "有效"
            and bool(record.get("description"))
            and bool(record.get("category", {}).get("leaf"))
            and bool(record.get("standard_specs"))
            and not flags.intersection({
                "TYPE1_TYPE2_ATTRIBUTE_CONFLICT",
                "DUPLICATE_STANDARD_SPEC",
                "MISSING_SPECIFICATIONS",
            })
        )
        if auto_ok:
            record["data_status"] = "ACTIVE"
            record["publication_basis"] = "AUTO_RULE_V1"
            auto_active.write(json.dumps(record, ensure_ascii=False) + "\n")
            auto_active_count += 1
type2_only_count = jsonl(type2_only_path, type2_only_docs())
jsonl(issues_path, iter(issues))
for code in sorted(set(type1) & set(type2)):
    compare_attrs(code, type1[code]["attrs"], type2[code])
jsonl(type2_audit_issues_path, iter(type2_audit_issues))

def price_candidates() -> Iterator[dict[str, Any]]:
    for code in sorted(type1):
        row = type1[code]["row"]
        if row.get("STATUS") != "有效":
            continue
        category_name = row.get("CATEGORY_NAME", "")
        short = category_name.split(".")[-1] if category_name else ""
        volatile = any(word in category_name for word in ("铜", "铝", "电缆", "铜排", "柜体", "箱体"))
        days = 90 if volatile else 180
        valid_to = today + timedelta(days=days) - timedelta(days=1)
        yield {
            "material_code": code,
            "unit_price_ex_tax": None,
            "pricing_unit": None,
            "tax_rate": 0.13,
            "price_version": "CN-MARKET-2026-V1",
            "valid_from": str(today),
            "valid_to": str(valid_to),
            "status": "PENDING",
            "source": "待按中国市场核价或同系列估算",
            "price_basis": "SAME_SERIES_ESTIMATE_ALLOWED",
            "confidence": "UNCONFIRMED",
            "category": short,
        }

price_count = jsonl(prices_path, price_candidates())
summary = {
    "source": str(SOURCE),
    "products_type1_rows": row_counts["1"],
    "type2_rows": row_counts["2"],
    "type1_pages": page_counts["1"],
    "type2_pages": page_counts["2"],
    "type1_status": dict(status_counts["1"]),
    "type2_status": dict(status_counts["2"]),
    "products_candidates": product_count,
    "products_pending_review": pending_count,
    "products_source_invalid_current": source_invalid_count,
    "products_auto_active_candidates": auto_active_count,
    "type2_only_candidates": type2_only_count,
    "price_candidates": price_count,
    "issue_count": len(issues),
    "type2_audit_issue_count": len(type2_audit_issues),
    "shared_codes": len(set(type1) & set(type2)),
    "type1_only_codes": len(set(type1) - set(type2)),
    "type2_only_codes": len(set(type2) - set(type1)),
    "encoding_repaired": True,
    "price_policy": {"standard_days": 180, "volatile_days": 90, "tax_rate_default": 0.13, "source_override_allowed": True},
    "outputs": {
        "products": str(products_path),
        "products_source_invalid_current": str(products_source_invalid_path),
        "products_auto_active_candidates": str(products_auto_active_path),
        "type2_only": str(type2_only_path),
        "issues": str(issues_path),
        "type2_audit_issues": str(type2_audit_issues_path),
        "prices": str(prices_path),
    },
}
(OUT / "field_mapping_report.json").write_text(json.dumps({
    "standard_keys": {
        "model": "型号说明/型号",
        "frame_current_a": "壳架电流等级/壳架电流/壳架",
        "rated_current_a": "额定电流/额定工作电流",
        "operating_current_a": "工作电流",
        "pole_count": "极数",
        "breaking_capacity": "分断能力/分断",
        "rated_voltage": "额定电压/额定工作电压",
        "operating_voltage": "工作电压",
        "control_voltage": "控制电压",
        "frequency_hz": "额定频率/频率",
        "trip_mechanism": "脱扣方式",
        "protection_type": "保护类型",
        "mounting_method": "安装方式",
        "accessory_code": "附件代码/附件代号",
        "accessory_voltage": "附件电压等级",
        "controller_type": "控制器类别",
        "special_note": "特殊说明",
        "ip_rating": "防护等级"
    },
    "mapping_is_conservative": True,
    "unmapped_attributes_remain_in_source_attributes": True
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
