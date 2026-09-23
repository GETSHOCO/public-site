#!/usr/bin/env python3
"""Small, same-origin Shoco launch-list endpoint."""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_BODY = 8 * 1024
RATE_WINDOW = 15 * 60
RATE_LIMIT = 8
DATABASE = Path(os.environ.get("SHOCO_MAILING_LIST_DB", "/data/mailing-list.sqlite3"))
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()
recent_requests: dict[str, list[float]] = {}


def client_ip(handler: BaseHTTPRequestHandler) -> str:
    forwarded = handler.headers.get("CF-Connecting-IP", "").strip()
    candidate = forwarded or handler.headers.get("X-Real-IP", "").strip() or handler.client_address[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "unknown"


def database() -> sqlite3.Connection:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            subscribed_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'website'
        )"""
    )
    return connection


def rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [stamp for stamp in recent_requests.get(ip, []) if now - stamp < RATE_WINDOW]
    attempts.append(now)
    recent_requests[ip] = attempts
    if len(recent_requests) > 2000:
        for key in list(recent_requests)[:500]:
            recent_requests.pop(key, None)
    return len(attempts) > RATE_LIMIT


def turnstile_ok(token: str, ip: str) -> bool:
    if not TURNSTILE_SECRET:
        return True
    if not token:
        return False
    payload = urllib.parse.urlencode({"secret": TURNSTILE_SECRET, "response": token, "remoteip": ip}).encode()
    request = urllib.request.Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read())
        return result.get("success") is True
    except Exception:
        return False


def page(title: str, message: str) -> bytes:
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{safe_title} — Shoco</title><style>body{{margin:0;display:grid;min-height:100vh;place-items:center;background:#f4f1ea;color:#101010;font:18px system-ui,sans-serif}}main{{width:min(560px,calc(100% - 40px));padding:48px 0}}a{{color:#ff5b18}}h1{{font-size:clamp(2.5rem,8vw,5rem);line-height:.95;letter-spacing:-.07em}}</style></head><body><main><p>SHOCO</p><h1>{safe_title}</h1><p>{safe_message}</p><a href=\"/\">Back to Shoco</a></main></body></html>""".encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "ShocoList/1.0"

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/subscribe":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self.headers.get("Origin") not in (None, "https://getshoco.com", "https://www.getshoco.com"):
            self.respond(HTTPStatus.FORBIDDEN, "Not available", "This signup endpoint only accepts requests from the Shoco website.")
            return
        ip = client_ip(self)
        if rate_limited(ip):
            self.respond(HTTPStatus.TOO_MANY_REQUESTS, "Try again later", "Please wait a little while before trying again.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 1 or length > MAX_BODY:
            self.respond(HTTPStatus.BAD_REQUEST, "Could not sign you up", "Please submit a valid email address.")
            return
        try:
            values = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            self.respond(HTTPStatus.BAD_REQUEST, "Could not sign you up", "Please submit a valid email address.")
            return
        if values.get("website", [""])[0].strip() or not turnstile_ok(values.get("cf-turnstile-response", [""])[0], ip):
            self.respond(HTTPStatus.OK, "Thanks for hiding in plain sight", "You are all set.")
            return
        email = values.get("email", [""])[0].strip().lower()
        if len(email) > 254 or not EMAIL_RE.fullmatch(email):
            self.respond(HTTPStatus.BAD_REQUEST, "That email needs a second look", "Please enter a valid email address.")
            return
        connection = database()
        try:
            connection.execute(
                "INSERT OR IGNORE INTO subscribers(email, subscribed_at) VALUES (?, datetime('now'))",
                (email,),
            )
            connection.commit()
        finally:
            connection.close()
        self.respond(HTTPStatus.OK, "You’re on the list", "We’ll send occasional Shoco updates as we get closer to launch.")

    def respond(self, status: HTTPStatus, title: str, message: str) -> None:
        body = page(title, message)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        # Never write email addresses or request bodies to container logs.
        return


if __name__ == "__main__":
    database().close()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
