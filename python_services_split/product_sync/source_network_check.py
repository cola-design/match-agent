"""Read-only source API diagnostics. Run beside product_sync/.env with Python 3.10+."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import errno
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


VERSION = "1.0.0"
BODY_LIMIT = 4 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def load_config():
    from dotenv import load_dotenv

    inherited = "PRODUCT_API_URL" in os.environ
    load_dotenv(Path(__file__).with_name(".env"), override=False, encoding="utf-8-sig")
    url = os.environ.get("PRODUCT_API_URL", "").strip()
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.hostname == "source.example.invalid"):
        raise ValueError("invalid source URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    start = os.environ.get("SOURCE_DATE_FROM", "").strip()
    date.fromisoformat(start)
    return {
        "url": url, "parsed": parsed, "port": port, "date_from": start,
        "key": os.environ.get("PRODUCT_API_KEY", "").strip(),
        "url_source": "process_environment" if inherited else "local_env_file",
    }


def network_error(exc):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        category = "DNS_ERROR"
    elif isinstance(reason, ssl.SSLCertVerificationError):
        category = "TLS_CERTIFICATE_VERIFY_FAILED"
    elif isinstance(reason, ssl.SSLError):
        category = "TLS_ERROR"
    elif isinstance(reason, (TimeoutError, socket.timeout)):
        category = "NETWORK_TIMEOUT"
    elif isinstance(reason, ConnectionRefusedError):
        category = "CONNECTION_REFUSED"
    elif isinstance(reason, OSError) and reason.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        category = "NETWORK_UNREACHABLE"
    elif isinstance(reason, str) and "Tunnel connection failed" in reason:
        category = "PROXY_TUNNEL_ERROR"
    else:
        category = "NETWORK_ERROR"
    # Do not serialize exception text: it can contain URLs or proxy credentials.
    return {"ok": False, "category": category, "error_type": type(reason).__name__}


def response_headers(response):
    value = response.headers.get("X-Squid-Error", "")
    match = re.match(r"(ERR_[A-Z_]+)(?:\s|$)", value)
    return {
        "http_status": int(response.code),
        "response_from_squid": "squid" in response.headers.get("Server", "").lower(),
        "squid_error": match.group(1) if match else None,
    }


def child_probe(config, kind, timeout, page_size):
    host, port = config["parsed"].hostname, config["port"]
    try:
        if kind == "dns":
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            emit({"event": "result", "ok": True, "category": "DNS_OK",
                  "addresses": sorted({item[4][0] for item in records})[:8]})
        elif kind == "tcp_direct":
            with socket.create_connection((host, port), timeout=timeout):
                emit({"event": "result", "ok": True, "category": "TCP_CONNECTED"})
        else:
            if not config["key"] or config["key"] == "CHANGE_THIS_SOURCE_API_KEY":
                emit({"event": "result", "ok": False, "category": "SOURCE_KEY_MISSING"})
                return
            params = urllib.parse.urlencode({
                "dateFrom": config["date_from"], "type": 1,
                "pageNum": 1, "pageSize": page_size,
            })
            request = urllib.request.Request(
                config["url"] + "?" + params,
                headers={"Accept": "application/json", "apikey": config["key"]},
            )
            # Same redirect and environment-proxy behavior as the production source client.
            opener = urllib.request.build_opener(NoRedirect())
            emit({"event": "http_opening"})
            with opener.open(request, timeout=timeout) as response:
                headers = response_headers(response)
                emit({"event": "http_headers", **headers})
                body = response.read(BODY_LIMIT + 1)
            if len(body) > BODY_LIMIT:
                emit({"event": "result", "ok": False, "category": "BODY_LIMIT_EXCEEDED"})
                return
            try:
                payload = json.loads(body.decode("utf-8-sig"))
            except (ValueError, UnicodeError):
                emit({"event": "result", "ok": False, "category": "INVALID_JSON"})
                return
            valid = (isinstance(payload, dict) and payload.get("status") is True
                     and isinstance(payload.get("data"), list))
            emit({"event": "result", "ok": valid,
                  "category": "SOURCE_FIRST_PAGE_OK" if valid else "SOURCE_BUSINESS_ERROR",
                  "data_rows": len(payload["data"]) if valid else None})
    except urllib.error.HTTPError as exc:
        emit({"event": "result", "ok": False, "category": "HTTP_ERROR",
              **response_headers(exc)})
        exc.close()
    except (urllib.error.URLError, OSError) as exc:
        emit({"event": "result", **network_error(exc)})
    except Exception as exc:
        emit({"event": "result", "ok": False, "category": "PROBE_ERROR",
              "error_type": type(exc).__name__})


def run_probe(kind, timeout, page_size):
    started = time.monotonic()
    command = [sys.executable, str(Path(__file__).resolve()), "--_probe", kind,
               "--timeout", str(timeout), "--page-size", str(page_size)]
    deadline = False
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 1)
        output = result.stdout
    except subprocess.TimeoutExpired as exc:
        deadline = True
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
    report = {"probe": kind, "ok": False, "category": "PROBE_PROCESS_FAILED"}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "http_headers":
            report.update({key: value for key, value in event.items() if key != "event"})
        elif event.get("event") == "result":
            report.update({key: value for key, value in event.items() if key != "event"})
    if deadline:
        report.update(ok=False, category="PROBE_DEADLINE_EXCEEDED")
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return report


def next_step(check):
    category = check.get("category")
    status = check.get("http_status")
    if check.get("ok"):
        return "源接口首屏已成功返回；重新运行 sync.py --check。此诊断不验证数据库或全量数据。"
    if category == "SOURCE_KEY_MISSING":
        return "在本机配置真实PRODUCT_API_KEY；该值来自产品源接口，不是查询API的密钥。"
    if category == "SOURCE_BUSINESS_ERROR":
        return "HTTP连接已成功，但源接口业务响应不符合约定；在服务器本机核对源接口密钥和配置。"
    if category == "DNS_ERROR":
        return "检查源域名的服务器DNS解析；如果必须经代理访问，同时核对代理配置。"
    if category == "TLS_CERTIFICATE_VERIFY_FAILED":
        return "核对服务器时间、CA证书及公司代理证书信任；保留证书校验。"
    if status == 407 or category == "PROXY_TUNNEL_ERROR":
        return "检查源请求所使用的出站代理及其认证配置。"
    if status in (401, 403):
        return "已收到HTTP响应；核对源接口密钥、来源IP许可，以及是否由代理拒绝访问。"
    if status in (301, 302, 307, 308, 404):
        return "已收到HTTP响应；与原来成功使用的接口地址核对协议、端口和路径。"
    if status == 200 and category in ("NETWORK_TIMEOUT", "PROBE_DEADLINE_EXCEEDED"):
        return "已收到HTTP响应头，但响应体未在限时内读完；检查源接口响应速度和数据传输。"
    if category in ("NETWORK_TIMEOUT", "PROBE_DEADLINE_EXCEEDED", "NETWORK_UNREACHABLE", "CONNECTION_REFUSED"):
        return "检查目标端口的出站路由、防火墙、代理或接口来源IP许可；不要仅增加重试次数。"
    return "按HTTP状态和category继续定位；不要发送.env、完整接口路径或密钥。"


def redact_strings(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "<redacted>") if secret else value
    if isinstance(value, list):
        return [redact_strings(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: redact_strings(item, secret) for key, item in value.items()}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--page-size", type=int, default=1)
    parser.add_argument("--_probe", choices=("dns", "tcp_direct", "http_configured"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 0.5 <= args.timeout <= 30 or not 1 <= args.page_size <= 1000:
        parser.error("timeout must be 0.5..30 seconds; page-size must be 1..1000")
    try:
        config = load_config()
    except Exception as exc:
        emit({"result": "config_error", "error_type": type(exc).__name__,
              "hint": "核对脚本同目录.env中的PRODUCT_API_URL、SOURCE_DATE_FROM及python-dotenv依赖。"})
        return 2
    if args._probe:
        child_probe(config, args._probe, args.timeout, args.page_size)
        return 0
    parsed = config["parsed"]
    proxy_configured = bool(urllib.request.getproxies().get(parsed.scheme))
    checks = []
    for kind in ("dns", "tcp_direct", "http_configured"):
        print("正在检查：" + kind, file=sys.stderr, flush=True)
        checks.append(run_probe(kind, args.timeout, args.page_size))
    report = {
        "diagnostic_version": VERSION,
        "checked_at_beijing": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "target": {"scheme": parsed.scheme, "host": parsed.hostname, "port": config["port"]},
        "url_config_source": config["url_source"],
        "source_key_configured": bool(config["key"]),
        "proxy_configured_for_scheme": proxy_configured,
        "proxy_bypass_for_target": bool(urllib.request.proxy_bypass(parsed.netloc)),
        "page_size": args.page_size,
        "probe_deadline_seconds": args.timeout + 1,
        "database_accessed": False,
        "checks": checks,
        "result": "source_first_page_ok" if checks[-1].get("ok") else "diagnostic_failed",
        "next_step": next_step(checks[-1]),
    }
    # Defensive redaction, including malformed hostnames that accidentally contain the key.
    print(json.dumps(redact_strings(report, config["key"]), ensure_ascii=False, indent=2))
    return 0 if checks[-1].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
