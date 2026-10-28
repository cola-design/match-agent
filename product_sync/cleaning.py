"""Shared pure product cleaning rules; importing never reads files or writes databases."""
from typing import Any
import json

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

def value_parts(value: str, field: str = "source_value") -> dict[str, Any]:
    from mongodb_api.product_contract import normalize_spec
    return normalize_spec({"value_text": value}, field)


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
        item = value_parts(attr["value"], key)
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
