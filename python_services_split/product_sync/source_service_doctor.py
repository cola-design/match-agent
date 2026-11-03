"""One-pass, read-only product-sync diagnostics; Python 3.10+, beside .env."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import errno
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "2.0.0"
ROOT = Path(__file__).resolve().parent
BODY_LIMIT = 32 * 1024 * 1024
FIELDS = ("PRODUCT_API_URL", "PRODUCT_API_KEY", "SOURCE_DATE_FROM", "SOURCE_PAGE_SIZE",
          "SYNC_MONGODB_URI", "MONGODB_DB", "SYNC_STATE_DIR")


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def valid_host(value):
    if not isinstance(value, str) or len(value) > 253:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return value == "localhost" or bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*\.[A-Za-z0-9-]+)", value))


def url_structure(raw):
    issues = []
    result = {"present": bool(raw), "valid": False, "scheme": None, "host": None,
              "port": None, "path_present": False, "query_present": False,
              "credentials_present": False, "issues": issues}
    if not raw:
        issues.append("URL_MISSING")
        return result
    if raw.startswith("PRODUCT_API_URL="):
        issues.append("REPEATED_VARIABLE_NAME")
    if any(c.isspace() for c in raw):
        issues.append("URL_CONTAINS_WHITESPACE")
    if raw[:1] in "‘’“”\"'" or raw[-1:] in "‘’“”\"'":
        issues.append("URL_CONTAINS_QUOTE_CHARACTERS")
    if raw.startswith("[") or "](" in raw:
        issues.append("MARKDOWN_LINK_IN_URL")
    try:
        parsed = urllib.parse.urlsplit(raw)
        result.update(scheme=parsed.scheme or None, host=parsed.hostname,
                      path_present=bool(parsed.path and parsed.path != "/"),
                      query_present=bool(parsed.query),
                      credentials_present=parsed.username is not None or parsed.password is not None)
        if parsed.scheme not in ("http", "https"):
            issues.append("HTTP_SCHEME_MISSING_OR_INVALID")
        if not parsed.hostname or not valid_host(parsed.hostname):
            issues.append("HOST_MISSING_OR_INVALID")
        if parsed.username is not None or parsed.password is not None:
            issues.append("CREDENTIALS_MUST_NOT_BE_IN_URL")
        if parsed.query:
            issues.append("QUERY_PARAMETERS_MUST_NOT_BE_IN_BASE_URL")
        if parsed.fragment:
            issues.append("FRAGMENT_MUST_NOT_BE_IN_URL")
        result["port"] = parsed.port or ({"https": 443, "http": 80}.get(parsed.scheme))
        if result["port"] is not None and not 1 <= result["port"] <= 65535:
            issues.append("PORT_INVALID")
        if parsed.hostname and parsed.hostname.endswith(".invalid"):
            issues.append("PLACEHOLDER_URL")
    except ValueError:
        issues.append("URL_PARSE_ERROR")
    result["valid"] = not issues
    return result


def read_config():
    before = {key: os.environ.get(key) for key in FIELDS}
    file_values, duplicates, invalid_lines = {}, [], []
    parser_available = True
    env_file = ROOT / ".env"
    try:
        from dotenv import dotenv_values, load_dotenv
        from dotenv.parser import parse_stream
        if env_file.is_file() and env_file.stat().st_size <= 1024 * 1024:
            text = env_file.read_text(encoding="utf-8-sig")
            seen = set()
            for binding in parse_stream(io.StringIO(text)):
                if binding.error:
                    invalid_lines.append(binding.original.line)
                if binding.key in FIELDS:
                    if binding.key in seen:
                        duplicates.append(binding.key)
                    seen.add(binding.key)
            file_values = dict(dotenv_values(stream=io.StringIO(text)))
            load_dotenv(stream=io.StringIO(text), override=False)
    except (ImportError, OSError, UnicodeError):
        parser_available = False
    values = {key: os.environ.get(key, "").strip() for key in FIELDS}
    values["MONGODB_DB"] = os.environ.get("MONGODB_DB", "match_agent")
    source = url_structure(values["PRODUCT_API_URL"])
    try:
        date.fromisoformat(values["SOURCE_DATE_FROM"])
        date_valid = True
    except ValueError:
        date_valid = False
    try:
        page_size = int(values["SOURCE_PAGE_SIZE"] or "1000")
        page_size_valid = 1 <= page_size <= 1000
    except ValueError:
        page_size, page_size_valid = 1000, False
    key_present = bool(values["PRODUCT_API_KEY"] and values["PRODUCT_API_KEY"] != "CHANGE_THIS_SOURCE_API_KEY")
    configuration = {
        "env_file_found": env_file.is_file(), "dotenv_available": parser_available,
        "env_parse_error_lines": invalid_lines,
        "duplicate_keys": sorted(set(duplicates)),
        "environment_overrides": [key for key in FIELDS if before[key] is not None],
        "environment_differs_from_file": [key for key in FIELDS if before[key] is not None
                                           and key in file_values and before[key] != file_values[key]],
        "source_url": source, "source_key_configured": key_present,
        "date_from_valid": date_valid, "page_size": page_size,
        "page_size_valid": page_size_valid,
        "mongodb_uri_configured": bool(values["SYNC_MONGODB_URI"]),
        "mongodb_database": values["MONGODB_DB"] if re.fullmatch(r"[\w.-]{1,64}", values["MONGODB_DB"]) else "<invalid>",
    }
    return values, configuration


def recent_runs(values):
    base = Path(values["SYNC_STATE_DIR"] or "sync_state")
    if not base.is_absolute():
        base = Path.cwd() / base
    rows, seen = [], set()
    try:
        paths = [base / "latest_check.json", base / "latest.json"] + sorted(base.glob("*/report.json"), reverse=True)[:20]
        for path in paths:
            try:
                if path.stat().st_size > 1024 * 1024:
                    continue
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                run_id = data.get("run_id")
                if not isinstance(run_id, str) or run_id in seen:
                    continue
                seen.add(run_id)
                allowed = ("run_id", "mode", "phase", "result", "failed_phase", "error",
                           "started_at", "finished_at", "started_at_beijing", "source_host",
                           "products_write_started", "prices_touched", "baseline_products")
                row = {key: data[key] for key in allowed if key in data and
                       isinstance(data[key], (str, int, float, bool, type(None)))}
                rows.append(row)
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return sorted(rows, key=lambda row: str(row.get("started_at", "")), reverse=True)[:8]


def diagnostic_target(values, configuration, history):
    source = configuration["source_url"]
    if source["host"] and valid_host(source["host"]) and source["port"]:
        return {"host": source["host"], "ports": [source["port"]],
                "basis": "configured_url" if source["valid"] else "parsed_invalid_url_connectivity_only",
                "authenticated_http_allowed": source["valid"]}
    # Derive a host for unauthenticated DNS/TCP checks only. Never repair the file.
    candidate = values["PRODUCT_API_URL"]
    if candidate.startswith("PRODUCT_API_URL="):
        candidate = candidate[len("PRODUCT_API_URL="):]
    candidate = candidate.strip("\"'‘’“” ")
    match = re.fullmatch(r"\[[^\]]*\]\((https?://[^\s]+)\)", candidate)
    if match:
        candidate = match.group(1)
    try:
        inferred = "://" not in candidate
        parsed = urllib.parse.urlsplit("//" + candidate if inferred else candidate)
        if valid_host(parsed.hostname) and parsed.username is None and parsed.password is None:
            explicit_port = parsed.port
            ports = [explicit_port] if explicit_port else ([80, 443] if inferred or parsed.scheme not in ("http", "https") else [443 if parsed.scheme == "https" else 80])
            return {"host": parsed.hostname, "ports": ports, "basis": "derived_for_connectivity_only",
                    "authenticated_http_allowed": False}
    except ValueError:
        pass
    for row in history:
        if valid_host(row.get("source_host")):
            return {"host": row["source_host"], "ports": [80, 443],
                    "basis": "recent_report_host_ports_not_confirmed", "authenticated_http_allowed": False}
    return None


def proxy_info(configuration):
    source = configuration["source_url"]
    scheme = source.get("scheme")
    proxies = urllib.request.getproxies()
    raw = proxies.get(scheme, "") if scheme else ""
    bypass = False
    if source.get("host"):
        try:
            bypass = bool(urllib.request.proxy_bypass(source["host"] + ":" + str(source["port"])))
        except Exception:
            pass
    result = {"configured_for_scheme": bool(raw), "bypass_for_target": bypass,
              "active_expected": bool(raw) and not bypass}
    if raw:
        try:
            parsed = urllib.parse.urlsplit(raw if "://" in raw else "http://" + raw)
            if valid_host(parsed.hostname):
                result.update(host=parsed.hostname, port=parsed.port or (443 if parsed.scheme == "https" else 80),
                              credentials_present=parsed.username is not None)
            else:
                result["format_invalid"] = True
        except ValueError:
            result["format_invalid"] = True
    return result


def classify_network(exc):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        code = "DNS_ERROR"
    elif isinstance(reason, ssl.SSLCertVerificationError):
        code = "TLS_CERTIFICATE_VERIFY_FAILED"
    elif isinstance(reason, ssl.SSLError):
        code = "TLS_ERROR"
    elif isinstance(reason, (TimeoutError, socket.timeout)):
        code = "NETWORK_TIMEOUT"
    elif isinstance(reason, ConnectionRefusedError):
        code = "CONNECTION_REFUSED"
    elif isinstance(reason, OSError) and reason.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        code = "NETWORK_UNREACHABLE"
    else:
        code = "NETWORK_ERROR"
    return {"ok": False, "category": code, "error_type": type(reason).__name__}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def headers(response):
    match = re.match(r"(ERR_[A-Z_]+)(?:\s|$)", response.headers.get("X-Squid-Error", ""))
    return {"http_status": int(response.code), "squid_error": match.group(1) if match else None,
            "response_from_squid": "squid" in response.headers.get("Server", "").lower()}


def http_probe(values, kind, page_size, timeout):
    params = urllib.parse.urlencode({"dateFrom": values["SOURCE_DATE_FROM"], "type": kind,
                                    "pageNum": 1, "pageSize": page_size})
    request = urllib.request.Request(values["PRODUCT_API_URL"] + "?" + params,
                                    headers={"Accept": "application/json", "apikey": values["PRODUCT_API_KEY"]})
    emit({"event": "http_opening"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            emit({"event": "http_headers", **headers(response)})
            body = response.read(BODY_LIMIT + 1)
        if len(body) > BODY_LIMIT:
            return {"ok": False, "category": "BODY_LIMIT_EXCEEDED"}
        try:
            payload = json.loads(body.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            return {"ok": False, "category": "INVALID_JSON"}
        if not isinstance(payload, dict) or payload.get("status") is not True or not isinstance(payload.get("data"), list):
            return {"ok": False, "category": "SOURCE_BUSINESS_ERROR"}
        rows = payload["data"]
        if not all(isinstance(row, dict) and isinstance(row.get("ITEM_NUM"), str) and row["ITEM_NUM"].strip() for row in rows):
            return {"ok": False, "category": "SOURCE_MATERIAL_CODE_INVALID"}
        if kind == 1 and not rows:
            return {"ok": False, "category": "EMPTY_PRODUCT_FIRST_PAGE"}
        return {"ok": True, "category": "SOURCE_PAGE_OK", "data_rows": len(rows),
                "page_size_honored": len(rows) <= page_size, "requested_page_size": page_size}
    except urllib.error.HTTPError as exc:
        result = {"ok": False, "category": "HTTP_ERROR", **headers(exc)}
        exc.close()
        return result
    except (urllib.error.URLError, OSError) as exc:
        return classify_network(exc)


def declared_privileges(document, database):
    auth = document.get("authInfo", {})
    privileges = auth.get("authenticatedUserPrivileges")
    if not privileges or not auth.get("authenticatedUsers"):
        return {"known": False}
    def allowed(collection, action):
        for privilege in privileges:
            resource = privilege.get("resource", {})
            matched = resource.get("anyResource") or (
                resource.get("db") in ("", database) and resource.get("collection") in ("", collection))
            actions = privilege.get("actions", [])
            if matched and (action in actions or "anyAction" in actions):
                return True
        return False
    return {"known": True, "products_find": allowed("products", "find"),
            "products_insert": allowed("products", "insert"), "products_update": allowed("products", "update"),
            "products_create_index": allowed("products", "createIndex"),
            "sync_run_insert": allowed("product_sync_runs", "insert"),
            "verification": "declared_privileges_only_no_writes_executed"}


def mongo_probe(values):
    try:
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
    except ImportError:
        return {"ok": False, "category": "PYMONGO_MISSING"}
    if not values["SYNC_MONGODB_URI"]:
        return {"ok": False, "category": "SYNC_MONGODB_URI_MISSING"}
    client, result, step = None, {"database_written": False}, "connect"
    try:
        client = MongoClient(values["SYNC_MONGODB_URI"], serverSelectionTimeoutMS=4000,
                             connectTimeoutMS=4000, socketTimeoutMS=7000, timeoutMS=10000)
        db = client[values["MONGODB_DB"]]
        db.command("ping")
        step = "products_read"
        db.products.find_one({}, {"_id": 1}, max_time_ms=3000)
        result["products_read"] = True
        result["product_count"] = db.products.count_documents({}, maxTimeMS=3000)
        step = "product_structure"
        types = {"material_code": "string", "category": "object", "standard_specs": "object",
                 "source_attributes": "array", "quality_flags": "array"}
        invalid = db.products.count_documents({"$or": [{key: {"$not": {"$type": kind}}}
                                             for key, kind in types.items()]}, limit=1, maxTimeMS=3000)
        result["product_structure_valid"] = not bool(invalid)
        step = "source_attribute_aggregate"
        attribute_rows = list(db.products.aggregate([
            {"$match": {"source_attributes.source": "type2"}}, {"$unwind": "$source_attributes"},
            {"$match": {"source_attributes.source": "type2"}}, {"$count": "rows"},
        ], maxTimeMS=3000))
        result["stored_type2_attribute_rows"] = attribute_rows[0]["rows"] if attribute_rows else 0
        try:
            indexes = list(db.products.list_indexes())
            result["unique_material_index_present"] = any(
                list(row["key"].items()) == [("material_code", 1)] and row.get("unique") for row in indexes)
        except PyMongoError as exc:
            result["index_inspection_error"] = {"error_type": type(exc).__name__, "mongo_code": getattr(exc, "code", None)}
        try:
            result["declared_permissions"] = declared_privileges(db.command("connectionStatus", showPrivileges=True), db.name)
        except PyMongoError:
            result["declared_permissions"] = {"known": False}
        result.update(ok=not bool(invalid), category="MONGODB_READ_OK" if not invalid else "PRODUCT_STRUCTURE_INVALID")
    except Exception as exc:
        result.update(ok=False, category="MONGODB_CHECK_FAILED", failed_step=step,
                      error_type=type(exc).__name__, mongo_code=getattr(exc, "code", None))
    finally:
        if client is not None:
            client.close()
    return result


def run_child(args):
    values, _ = read_config()
    try:
        if args._probe == "dns":
            records = socket.getaddrinfo(args.host, args.port, type=socket.SOCK_STREAM)
            result = {"ok": True, "category": "DNS_OK", "addresses": sorted({row[4][0] for row in records})[:8]}
        elif args._probe == "tcp":
            with socket.create_connection((args.host, args.port), timeout=args.timeout) as connection:
                result = {"ok": True, "category": "TCP_CONNECTED", "peer_ip": connection.getpeername()[0]}
        elif args._probe == "mongo":
            result = mongo_probe(values)
        else:
            result = http_probe(values, args.source_type, args.page_size, args.timeout)
    except (urllib.error.URLError, OSError) as exc:
        result = classify_network(exc)
    except Exception as exc:
        result = {"ok": False, "category": "PROBE_ERROR", "error_type": type(exc).__name__}
    emit({"event": "result", **result})


def run_probe(kind, timeout, host=None, port=None, source_type=1, page_size=1):
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--_probe", kind,
               "--timeout", str(timeout), "--source-type", str(source_type), "--page-size", str(page_size)]
    if host:
        command += ["--host", host, "--port", str(port)]
    started, deadline = time.monotonic(), False
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 1)
        output = completed.stdout
    except subprocess.TimeoutExpired as exc:
        deadline, output = True, exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
    result = {"ok": False, "category": "PROBE_PROCESS_FAILED"}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") in ("result", "http_headers"):
            result.update({key: value for key, value in event.items() if key != "event"})
    if deadline:
        result.update(ok=False, category="PROBE_DEADLINE_EXCEEDED")
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return result


def command_output(command, timeout=4):
    if not shutil.which(command[0]):
        return None, "COMMAND_UNAVAILABLE"
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            return None, "COMMAND_DENIED_OR_FAILED"
        return result.stdout, None
    except subprocess.TimeoutExpired:
        return None, "COMMAND_TIMEOUT"


def ufw_snapshot(target):
    output, error = command_output(["ufw", "status", "verbose"])
    if error:
        return {"available": False, "category": error}
    policy = re.search(r"(\w+)\s*\(outgoing\)", output)
    ports = target["ports"] if target else []
    rules = [line.strip() for line in output.splitlines() if re.search(r"(?:ALLOW|DENY|REJECT)\s+OUT", line)
             and (target and target["host"] in line or any(re.search(r"(?<!\d)" + str(port) + r"(?!\d)", line) for port in ports))]
    return {"available": True, "active": bool(re.search(r"Status:\s*active\b", output)),
            "default_outgoing": policy.group(1).lower() if policy else None,
            "possibly_relevant_outgoing_rules": rules[:20],
            "rule_matching_is_advisory": True}


def output_chain_snapshot():
    output, error = command_output(["iptables", "-w", "2", "-nvL", "OUTPUT", "--line-numbers"])
    if error:
        return {"available": False, "category": error}
    policy = re.search(r"Chain OUTPUT \(policy (\w+)", output)
    return {"available": True, "default_policy": policy.group(1) if policy else None,
            "direct_drop_or_reject_rules": sum(bool(re.search(r"\b(?:DROP|REJECT)\b", line))
                                               for line in output.splitlines()[2:]),
            "scope": "OUTPUT_base_chain_only_nested_chains_not_proven_clear"}


def cron_snapshot():
    output, error = command_output(["crontab", "-l"])
    offset = int(getattr(time.localtime(), "tm_gmtoff", -time.timezone))
    result = {"scope": "current_user_only", "system_utc_offset_seconds": offset,
              "current_user_jobs": [], "category": error or "CRONTAB_READ_OK"}
    if error:
        return result
    cron_tz = None
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("CRON_TZ="):
            cron_tz = stripped.split("=", 1)[1].strip("\"' ")
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", stripped):
            continue
        if "sync.py" not in stripped and "product_sync.sync" not in stripped:
            continue
        fields = stripped.split(None, 5)
        if len(fields) != 6 or not all(re.fullmatch(r"[0-9*/,-]+", field) for field in fields[:5]):
            result["current_user_jobs"].append({"schedule_parseable": False})
            continue
        schedule, command = fields[:5], fields[5]
        standard = schedule == ["0", "1", "1", "*", "*"] and offset == 8 * 3600 and not cron_tz
        utc_gate = (schedule == ["0", "17", "28-31", "*", "*"] and offset == 0
                    and "TZ=Asia/Shanghai" in command and "%d" in command and "01" in command and not cron_tz)
        result["current_user_jobs"].append({"schedule_parseable": True, "schedule": schedule,
            "apply_mode": bool(re.search(r"(?:^|\s)--apply(?:\s|$)", command)),
            "monthly_beijing_01_recognized": standard or utc_gate,
            "cron_tz_present": bool(cron_tz),
            "cron_tz_support_not_assumed": bool(cron_tz)})
    return result


def route_snapshot(address):
    family = "-6" if ipaddress.ip_address(address).version == 6 else "-4"
    output, error = command_output(["ip", "-j", family, "route", "get", address])
    if error:
        return {"target_ip": address, "available": False, "category": error}
    try:
        entries = json.loads(output)
        entry = entries[0] if entries else {}
        result = {"target_ip": address, "available": True,
                  **{key: entry[key] for key in ("gateway", "dev", "prefsrc", "src", "type") if key in entry}}
        dev = entry.get("dev", "")
        result["routed_via_docker_bridge"] = dev.startswith("br-") or dev == "docker0"
        if not entry.get("gateway"):
            neighbor, neighbor_error = command_output(["ip", "-j", family, "neigh", "show", "to", address], timeout=2)
            if not neighbor_error:
                rows = json.loads(neighbor)
                states = [row.get("state", []) for row in rows]
                result["neighbor_states"] = [state for entry in states
                                             for state in ([entry] if isinstance(entry, str) else entry)][:10]
        return result
    except (ValueError, TypeError):
        return {"target_ip": address, "available": False, "category": "ROUTE_OUTPUT_UNPARSEABLE"}


def batch(jobs):
    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(function): name for name, function in jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = {"ok": False, "category": "COLLECTOR_FAILED", "error_type": type(exc).__name__}
    return results


def summarize(config, target, proxy, checks, system, routes):
    issues = []
    def issue(code, action, evidence=None, severity="error"):
        issues.append({"code": code, "severity": severity, "evidence": evidence, "action": action})
    source = config["source_url"]
    if not source["valid"]:
        issue("SOURCE_URL_FORMAT_INVALID", "修正PRODUCT_API_URL，使用原来成功的完整HTTP(S)接口地址；保留接口路径，密钥和分页参数分别配置。", source["issues"])
    if config["environment_differs_from_file"]:
        issue("ENVIRONMENT_OVERRIDES_FILE", "当前进程环境变量覆盖了.env；核对这些字段的启动配置。", config["environment_differs_from_file"], "warning")
    if config["duplicate_keys"] or config["env_parse_error_lines"]:
        issue("ENV_FILE_REVIEW", "核对.env重复字段或无法解析的行。", {"duplicates": config["duplicate_keys"], "error_lines": config["env_parse_error_lines"]})
    if not config["source_key_configured"]:
        issue("SOURCE_KEY_MISSING", "配置产品源接口的PRODUCT_API_KEY，不能使用查询API的密钥。")
    if not config["date_from_valid"] or not config["page_size_valid"]:
        issue("SOURCE_PARAMETERS_INVALID", "核对SOURCE_DATE_FROM为YYYY-MM-DD，SOURCE_PAGE_SIZE为1到1000。")
    mongo = checks.get("mongodb", {})
    if not mongo.get("ok"):
        code = mongo.get("mongo_code")
        action = ("核对同步MongoDB账号、密码及authSource。" if code == 18 else
                  "核对同步账号对目标库的读取权限。" if code == 13 else "根据MongoDB检测阶段和错误码修正连接或产品结构。")
        issue("MONGODB_CHECK_FAILED", action, {key: mongo.get(key) for key in ("category", "failed_step", "mongo_code")})
    declared = mongo.get("declared_permissions", {})
    if mongo.get("index_inspection_error"):
        issue("INDEX_INSPECTION_FAILED", "正式更新需要读取产品索引；让数据库维护人核对listIndexes权限。", mongo["index_inspection_error"])
    if declared.get("known"):
        missing = [key for key in ("products_insert", "products_update", "sync_run_insert") if not declared.get(key)]
        if not mongo.get("unique_material_index_present") and not declared.get("products_create_index"):
            missing.append("products_create_index")
        if missing:
            issue("DECLARED_WRITE_PRIVILEGES_MISSING", "让数据库维护人配置专用同步账号的目标库权限；本工具不试写、不授予权限。", missing)
    elif mongo.get("ok"):
        issue("WRITE_PRIVILEGES_NOT_CONFIRMED", "服务端未提供可用的权限声明；首次正式更新仍需备份并验证实际写入。", severity="warning")
    http_checks = [value for key, value in checks.items() if key.startswith("http_")]
    healthy_http = any(row.get("ok") for row in http_checks)
    for key in ("http_type1_small", "http_type2_small", "http_type1_batch", "http_type2_batch"):
        row = checks.get(key)
        if key.endswith("_batch") and row and row.get("ok") and row.get("page_size_honored") is False:
            issue("SOURCE_PRODUCTION_PAGE_SIZE_IGNORED", "源接口返回数量超过正式请求页大小；需接口维护人修正分页行为。", {"probe": key})
        if not row or row.get("ok"):
            continue
        status, category = row.get("http_status"), row.get("category")
        if status in (401, 403):
            issue("SOURCE_HTTP_ACCESS_DENIED", "已收到HTTP响应；核对源接口密钥、来源IP许可及代理拒绝标记。", {"probe": key, "status": status, "squid_error": row.get("squid_error")})
        elif status == 407:
            issue("PROXY_AUTHENTICATION_REQUIRED", "核对出站代理的认证配置。", {"probe": key})
        elif category == "TLS_CERTIFICATE_VERIFY_FAILED":
            issue("TLS_TRUST_FAILURE", "核对服务器时间、CA证书和公司代理证书信任；保留TLS校验。", {"probe": key})
        elif category in ("NETWORK_TIMEOUT", "PROBE_DEADLINE_EXCEEDED", "NETWORK_UNREACHABLE", "CONNECTION_REFUSED", "DNS_ERROR"):
            issue("SOURCE_REQUEST_NETWORK_FAILURE", "结合路由、直连TCP、代理和出站策略定位；单端超时不能证明是远端防火墙还是中间网络丢包。", {"probe": key, "category": category, "headers_received": bool(status)})
        else:
            issue("SOURCE_RESPONSE_INVALID", "核对完整接口路径和返回格式；报告不包含响应正文。", {"probe": key, "status": status, "category": category})
    tcp = [row for key, row in checks.items() if key.startswith("tcp_")]
    if tcp and not any(row.get("ok") for row in tcp) and not healthy_http:
        issue("TCP_CONNECTIVITY_FAILED", "目标TCP连接未建立；核对服务器到接口地址的路由、出站许可及目标服务监听。",
              [{key: row.get(key) for key in ("target_ip", "target_port", "category")} for row in tcp])
    if checks.get("dns_source", {}).get("category") == "DNS_ERROR" and not healthy_http:
        issue("SOURCE_DNS_FAILED", "核对源域名的服务器DNS解析及公司网络/VPN提供的DNS记录。")
    for route in routes:
        if route.get("routed_via_docker_bridge"):
            issue("POSSIBLE_DOCKER_SUBNET_OVERLAP", "确认源接口是否应位于本机Docker网络；若不是，核对该Docker子网是否与公司内网冲突，勿重启整个Docker。", route, "warning")
        if set(route.get("neighbor_states", [])) & {"FAILED", "INCOMPLETE"}:
            issue("ON_LINK_NEIGHBOR_UNRESOLVED", "内核把目标当作直连邻居但未解析成功；核对二层可达性、子网掩码与正确网关。", route)
    ufw = system.get("ufw", {})
    if ufw.get("active") and ufw.get("default_outgoing") in ("deny", "reject") and not healthy_http:
        issue("UFW_OUTBOUND_POLICY_REVIEW", "出站默认拒绝；由维护人核对到实际源接口或代理IP及端口的精确许可，不关闭整个防火墙。", ufw)
    batch_rows = [checks.get("http_type1_batch"), checks.get("http_type2_batch")]
    small_good = all(checks.get(key, {}).get("ok") for key in ("http_type1_small", "http_type2_small"))
    if small_good and any(row and not row.get("ok") for row in batch_rows):
        issue("PRODUCTION_PAGE_CHECK_FAILED", "小页可用但正式分页检测未通过；核对分页性能、页大小和响应耗时后调整SOURCE_PAGE_SIZE。")
    cron = system.get("cron", {})
    jobs = cron.get("current_user_jobs", [])
    if not jobs:
        issue("CURRENT_USER_SCHEDULE_NOT_FOUND", "当前账号未找到同步定时任务；若由其他账号调度，请部署负责人核对。", severity="warning")
    elif not any(job.get("apply_mode") and job.get("monthly_beijing_01_recognized") for job in jobs):
        issue("SCHEDULE_REVIEW", "核对正式任务使用--apply，并按北京时间每月1日01:00运行；CRON_TZ是否有效取决于cron实现。", jobs, "warning")
    read_only_limits = ["未拉取完整产品快照", "未写入MongoDB或验证实际写入成功", "未修改.env、路由、防火墙或定时任务",
                        "单端连接超时无法独立确定中间网络与远端设备的具体丢包位置"]
    result = "issues_found" if any(row["severity"] == "error" for row in issues) else "read_only_checks_passed"
    return result, issues, read_only_limits


def redact(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "<redacted>")
        return value
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, secrets) for key, item in value.items()}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--batch-timeout", type=float, default=30)
    parser.add_argument("--report", action="store_true", help="Create one new sanitized JSON report in diagnostic_reports")
    parser.add_argument("--_probe", choices=("dns", "tcp", "http", "mongo"), help=argparse.SUPPRESS)
    parser.add_argument("--host", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--source-type", type=int, choices=(1, 2), default=1, help=argparse.SUPPRESS)
    parser.add_argument("--page-size", type=int, default=1, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 0.5 <= args.timeout <= 30 or not 0.5 <= args.batch_timeout <= 30:
        parser.error("timeouts must be 0.5..30 seconds")
    if args._probe:
        run_child(args)
        return 0
    started = time.monotonic()
    values, config = read_config()
    history = recent_runs(values)
    target = diagnostic_target(values, config, history)
    proxy = proxy_info(config)
    print("一次诊断：配置、MongoDB、DNS、TCP、HTTP、代理、路由、防火墙和当前账号定时任务。", file=sys.stderr, flush=True)
    jobs = {
        "mongodb": lambda: run_probe("mongo", max(args.timeout, 12)),
        "ufw": lambda: ufw_snapshot(target), "output_chain": output_chain_snapshot,
        "cron": cron_snapshot,
    }
    if target:
        jobs["dns_source"] = lambda: run_probe("dns", args.timeout, target["host"], target["ports"][0])
    if proxy.get("active_expected") and proxy.get("host"):
        jobs["dns_proxy"] = lambda: run_probe("dns", args.timeout, proxy["host"], proxy["port"])
    first = batch(jobs)
    system = {key: first.pop(key) for key in ("ufw", "output_chain", "cron")}
    checks = first
    jobs, address_targets = {}, []
    if target:
        addresses = checks.get("dns_source", {}).get("addresses", [])[:2]
        for address in addresses:
            for port in target["ports"]:
                name = "tcp_source_" + address + "_" + str(port)
                def tcp_job(address=address, port=port):
                    return {**run_probe("tcp", args.timeout, address, port), "target_ip": address, "target_port": port}
                jobs[name] = tcp_job
            address_targets.append(address)
    if proxy.get("active_expected") and proxy.get("host"):
        for address in checks.get("dns_proxy", {}).get("addresses", [])[:2]:
            def proxy_job(address=address):
                return {**run_probe("tcp", args.timeout, address, proxy["port"]), "target_ip": address, "target_port": proxy["port"]}
            jobs["tcp_proxy_" + address] = proxy_job
            address_targets.append(address)
    http_allowed = config["source_url"]["valid"] and config["source_key_configured"] and config["date_from_valid"]
    if http_allowed:
        for kind in (1, 2):
            jobs["http_type" + str(kind) + "_small"] = lambda kind=kind: run_probe("http", args.timeout, source_type=kind, page_size=1)
    checks.update(batch(jobs))
    jobs = {"route_" + address: lambda address=address: route_snapshot(address) for address in set(address_targets)}
    small_good = all(checks.get(key, {}).get("ok") for key in ("http_type1_small", "http_type2_small"))
    if small_good and config["page_size_valid"]:
        print("两类小页已返回，正在核对正式分页大小。", file=sys.stderr, flush=True)
        for kind in (1, 2):
            jobs["http_type" + str(kind) + "_batch"] = lambda kind=kind: run_probe("http", args.batch_timeout, source_type=kind, page_size=config["page_size"])
    third = batch(jobs)
    routes = [value for key, value in third.items() if key.startswith("route_")]
    checks.update({key: value for key, value in third.items() if not key.startswith("route_")})
    result, issues, limits = summarize(config, target, proxy, checks, system, routes)
    report = {"diagnostic_version": VERSION, "checked_at_beijing": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
              "elapsed_seconds": round(time.monotonic() - started, 2), "python_version": sys.version.split()[0],
              "result": result, "configuration": config, "diagnostic_target": target, "proxy": proxy,
              "checks": checks, "system": system, "routes": routes, "recent_runs": history,
              "issues": issues, "verification_limits": limits, "database_written": False, "configuration_modified": False}
    secrets = [values["PRODUCT_API_KEY"], values["SYNC_MONGODB_URI"], values["PRODUCT_API_URL"]]
    try:
        password = urllib.parse.urlsplit(values["SYNC_MONGODB_URI"]).password
        if password:
            secrets.extend([password, urllib.parse.unquote(password)])
    except ValueError:
        pass
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        raw = os.environ.get(name, "")
        if raw:
            secrets.append(raw)
            try:
                password = urllib.parse.urlsplit(raw).password
                if password:
                    secrets.extend([password, urllib.parse.unquote(password)])
            except ValueError:
                pass
    report = redact(report, secrets)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        folder = ROOT / "diagnostic_reports"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        path = folder / ("source_service_diagnostic_" + stamp + "_" + uuid.uuid4().hex[:6] + ".json")
        with path.open("x", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
        print("诊断完成：" + result)
        for row in report["issues"]:
            print("[" + row["severity"] + "] " + row["code"] + "：" + row["action"])
        print("完整脱敏报告：" + str(path))
    else:
        print(encoded)
    return 0 if result == "read_only_checks_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
