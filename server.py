#!/usr/bin/env python3
"""HomeOps: authenticated dashboard, SQLite history, and Ubuntu live collector."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DB_PATH, SERVICES_PATH = ROOT / "homeops.db", ROOT / "services.json"
PORT = int(os.getenv("HOMEOPS_PORT", "13000"))
USERNAME = os.getenv("HOMEOPS_ADMIN_USER", "admin")
INITIAL_PASSWORD = os.getenv("HOMEOPS_ADMIN_PASSWORD")
SESSION_SECONDS = 43200
FRPS_URL = os.getenv("HOMEOPS_FRPS_METRICS_URL", "http://127.0.0.1:7500/metrics")
FRPS_USER = os.getenv("HOMEOPS_FRPS_USER", "")
FRPS_PASSWORD = os.getenv("HOMEOPS_FRPS_PASSWORD", "")
LOGIN_WINDOW_SECONDS, LOGIN_MAX_ATTEMPTS = 900, 5
HEALTH_CACHE_SECONDS = 20
DB_LOCK, HEALTH_CACHE_LOCK = threading.Lock(), threading.Lock()
HEALTH_CACHE = {}


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def initialize_db():
    con = db()
    con.execute("PRAGMA journal_mode = WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS users "
        "(username TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS sessions "
        "(token TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at INTEGER NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS net_samples "
        "(interface TEXT PRIMARY KEY, rx INTEGER NOT NULL, tx INTEGER NOT NULL, sampled_at REAL NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS bandwidth_samples "
        "(interface TEXT NOT NULL, download_mbps REAL NOT NULL, upload_mbps REAL NOT NULL, sampled_at REAL NOT NULL)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS bandwidth_samples_sampled_at "
        "ON bandwidth_samples(sampled_at)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS login_attempts "
        "(username TEXT NOT NULL, attempted_at INTEGER NOT NULL)"
    )
    con.commit()
    con.close()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)


def seed_admin():
    initialize_db()
    con = db()
    exists = con.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if not exists:
        if not INITIAL_PASSWORD:
            raise RuntimeError("Set HOMEOPS_ADMIN_PASSWORD before first start")
        salt = secrets.token_bytes(16)
        con.execute(
            "INSERT INTO users VALUES (?, ?, ?)",
            (USERNAME, salt, password_hash(INITIAL_PASSWORD, salt)),
        )
        con.commit()
    con.close()


def cleanup_expired():
    with DB_LOCK:
        con = db()
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        con.execute(
            "DELETE FROM login_attempts WHERE attempted_at < ?",
            (int(time.time()) - LOGIN_WINDOW_SECONDS,),
        )
        con.execute(
            "DELETE FROM bandwidth_samples WHERE sampled_at < ?",
            (time.time() - 30 * 24 * 3600,),
        )
        con.commit()
        con.close()


def cleanup_worker():
    while True:
        time.sleep(3600)
        cleanup_expired()


def load_services():
    if not SERVICES_PATH.exists():
        return []
    try:
        value = json.loads(SERVICES_PATH.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            return []
        services = []
        for item in value:
            if isinstance(item, dict):
                service = dict(item)
                if "healthUrl" not in service and isinstance(service.get("url"), str):
                    service["healthUrl"] = service["url"]
                services.append(service)
        return services
    except (OSError, json.JSONDecodeError):
        return []


def validate_services(value):
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("Services must be a list with at most 100 entries")
    services = []
    allowed_colors = {"green", "violet", "blue", "orange", "gray"}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each service must be an object")
        service = {}
        for key, limit in {
            "name": 100,
            "host": 255,
            "healthUrl": 2048,
            "tunnel": 100,
            "localTarget": 255,
            "icon": 16,
        }.items():
            entry = item.get(key, "")
            if not isinstance(entry, str) or len(entry.strip()) > limit:
                raise ValueError(f"Invalid {key}")
            service[key] = entry.strip()
        if not service["name"] or not service["host"]:
            raise ValueError("Service name and host are required")
        if service["healthUrl"]:
            parsed = urlparse(service["healthUrl"])
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Health URL must be an HTTP or HTTPS URL")
        color = item.get("color", "gray")
        if color not in allowed_colors:
            raise ValueError("Invalid color")
        service["color"] = color
        services.append(service)
    return services


def save_services(services):
    contents = json.dumps(services, indent=2, ensure_ascii=False) + "\n"
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=ROOT, prefix=".services-", suffix=".tmp", delete=False
    ) as temporary:
        temporary.write(contents)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, SERVICES_PATH)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def default_interface():
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 2 and fields[1] == "00000000":
                return fields[0]
    except OSError:
        pass
    return "eth0"


def net_rate():
    interface = default_interface()
    rx = tx = 0
    try:
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, values = line.split(":", 1)
            if name.strip() == interface:
                fields = values.split()
                rx, tx = int(fields[0]), int(fields[8])
                break
    except (OSError, ValueError, IndexError):
        return interface, 0, 0

    now = time.time()
    with DB_LOCK:
        con = db()
        prior = con.execute(
            "SELECT rx,tx,sampled_at FROM net_samples WHERE interface=?", (interface,)
        ).fetchone()
        con.execute(
            "INSERT OR REPLACE INTO net_samples VALUES (?,?,?,?)",
            (interface, rx, tx, now),
        )
        if prior and now > prior[2]:
            seconds = now - prior[2]
            down = max(0, (rx - prior[0]) * 8 / seconds / 1_000_000)
            up = max(0, (tx - prior[1]) * 8 / seconds / 1_000_000)
            con.execute(
                "INSERT INTO bandwidth_samples VALUES (?,?,?,?)",
                (interface, down, up, now),
            )
        else:
            down = up = 0
        con.commit()
        con.close()
    if not prior or now <= prior[2]:
        return interface, 0, 0
    return interface, down, up


def bandwidth_history(interface, hours):
    cutoff = time.time() - hours * 3600
    con = db()
    rows = con.execute(
        "SELECT download_mbps, upload_mbps, sampled_at FROM bandwidth_samples "
        "WHERE interface = ? AND sampled_at >= ? ORDER BY sampled_at",
        (interface, cutoff),
    ).fetchall()
    con.close()
    return [
        {
            "downloadMbps": round(download, 2),
            "uploadMbps": round(upload, 2),
            "sampledAt": round(sampled_at),
        }
        for download, upload, sampled_at in rows
    ]


def frps_online():
    request = Request(FRPS_URL)
    if FRPS_USER or FRPS_PASSWORD:
        token = base64.b64encode(f"{FRPS_USER}:{FRPS_PASSWORD}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urlopen(request, timeout=3) as response:
            return response.status == 200
    except (HTTPError, TimeoutError, URLError):
        return False


def check_service(item):
    url = item.get("healthUrl") or item.get("url") or ""
    if not isinstance(url, str) or not url:
        return "Unknown", "—"
    now = time.monotonic()
    with HEALTH_CACHE_LOCK:
        cached = HEALTH_CACHE.get(url)
        if cached and now - cached[0] < HEALTH_CACHE_SECONDS:
            return cached[1], cached[2]

    started = time.monotonic()
    try:
        with urlopen(
            Request(url, headers={"User-Agent": "HomeOps/1.0"}), timeout=7
        ) as response:
            result = (
                "Online" if 200 <= response.status < 400 else "Offline",
                f"{round((time.monotonic() - started) * 1000)} ms",
            )
    except (HTTPError, TimeoutError, URLError, ValueError):
        result = "Offline", "—"
    with HEALTH_CACHE_LOCK:
        HEALTH_CACHE[url] = (now, *result)
    return result


def dashboard():
    interface, down, up = net_rate()
    frps = frps_online()
    configured = [item for item in load_services() if isinstance(item, dict)]
    services, tunnels, alerts = [], [], []
    if not configured:
        alerts.append(
            [
                "warning",
                "No services configured",
                "services.json",
                "Copy services.example.json to services.json and add your real URLs.",
            ]
        )
    if not frps:
        alerts.append(
            [
                "warning",
                "FRPS metrics unavailable",
                FRPS_URL,
                "Enable the localhost FRPS web server and Prometheus metrics, then set credentials.",
            ]
        )

    with ThreadPoolExecutor(max_workers=min(8, len(configured) or 1)) as executor:
        service_statuses = list(executor.map(check_service, configured))
    for item, (status, ms) in zip(configured, service_statuses):
        name = item.get("name", "Unnamed service")
        host = item.get("host", item.get("url", "—"))
        services.append(
            {
                "name": name,
                "host": host,
                "icon": item.get("icon", "◈"),
                "color": item.get("color", "gray"),
                "status": status,
                "ms": ms,
            }
        )
        tunnels.append(
            [
                item.get("tunnel", name.lower().replace(" ", "-")),
                item.get("localTarget", "—"),
                host,
                "Online"
                if frps and status == "Online"
                else ("Offline" if status == "Offline" else "Unknown"),
                ms,
            ]
        )
        if status == "Offline":
            alerts.append(
                [
                    "warning",
                    f"{name} health check failed",
                    host,
                    "The public health-check URL did not return a successful response.",
                ]
            )

    network = [
        [
            "VPS NETWORK",
            interface,
            f"Download {down:.2f} Mbps · Upload {up:.2f} Mbps",
            "Live from /proc/net/dev",
        ],
        [
            "FRPS METRICS",
            "127.0.0.1:7500",
            "Prometheus endpoint",
            "Online" if frps else "Unavailable",
        ],
        [
            "SERVICE CHECKS",
            f"{len(services)} configured",
            "HTTPS health checks via public routes",
            f"{sum(x['status'] == 'Online' for x in services)} online",
        ],
    ]
    return {
        "services": services,
        "tunnels": tunnels,
        "network": network,
        "alerts": alerts,
        "bandwidth": {
            "interface": interface,
            "downloadMbps": round(down, 2),
            "uploadMbps": round(up, 2),
        },
    }


class HomeOpsHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("HomeOps:", fmt % args)

    def end_headers(self):
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def cookie_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == "homeops_session":
                return value
        return None

    def authenticated(self):
        token = self.cookie_token()
        if not token:
            return False
        con = db()
        row = con.execute(
            "SELECT expires_at FROM sessions WHERE token=?", (token,)
        ).fetchone()
        con.close()
        return bool(row and row[0] > int(time.time()))

    def claim_login_attempt(self, username):
        now = int(time.time())
        with DB_LOCK:
            con = db()
            con.execute(
                "DELETE FROM login_attempts WHERE attempted_at < ?",
                (now - LOGIN_WINDOW_SECONDS,),
            )
            attempts = con.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE username=?", (username,)
            ).fetchone()[0]
            if attempts < LOGIN_MAX_ATTEMPTS:
                con.execute("INSERT INTO login_attempts VALUES (?,?)", (username, now))
            con.commit()
            con.close()
        return attempts < LOGIN_MAX_ATTEMPTS

    def clear_login_attempts(self, username):
        with DB_LOCK:
            con = db()
            con.execute("DELETE FROM login_attempts WHERE username=?", (username,))
            con.commit()
            con.close()

    def json(self, status, value, cookie=None):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def request_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 100_000:
                raise ValueError
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return None
        return value

    def do_POST(self):
        if self.path == "/api/auth/login":
            payload = self.request_json()
            if payload is None:
                return self.json(400, {"error": "Invalid request"})
            if not isinstance(payload, dict):
                return self.json(400, {"error": "Invalid request"})
            username = payload.get("username", "")
            password = payload.get("password", "")
            if not isinstance(username, str) or not isinstance(password, str):
                return self.json(400, {"error": "Invalid request"})
            if not 1 <= len(username) <= 128 or not 1 <= len(password) <= 1024:
                return self.json(400, {"error": "Invalid request"})

            if not self.claim_login_attempt(username):
                return self.json(429, {"error": "Too many login attempts. Try again later."})

            con = db()
            row = con.execute(
                "SELECT salt,password_hash FROM users WHERE username=?", (username,)
            ).fetchone()
            con.close()
            valid = bool(
                row and hmac.compare_digest(password_hash(password, row[0]), row[1])
            )
            if not valid:
                return self.json(401, {"error": "Invalid credentials"})

            token = secrets.token_urlsafe(32)
            expires = int(time.time()) + SESSION_SECONDS
            with DB_LOCK:
                con = db()
                con.execute(
                    "DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),)
                )
                con.execute(
                    "INSERT INTO sessions VALUES (?,?,?)", (token, username, expires)
                )
                con.commit()
                con.close()
            self.clear_login_attempts(username)
            return self.json(
                200,
                {"ok": True},
                f"homeops_session={token}; HttpOnly; SameSite=Strict; Path=/; "
                f"Secure; Max-Age={SESSION_SECONDS}",
            )
        if self.path == "/api/auth/logout":
            token = self.cookie_token()
            if token:
                with DB_LOCK:
                    con = db()
                    con.execute("DELETE FROM sessions WHERE token=?", (token,))
                    con.commit()
                    con.close()
            return self.json(
                200,
                {"ok": True},
                "homeops_session=; HttpOnly; SameSite=Strict; Path=/; Secure; Max-Age=0",
            )
        return self.json(404, {"error": "Not found"})

    def do_PUT(self):
        if self.path != "/api/services":
            return self.json(404, {"error": "Not found"})
        if not self.authenticated():
            return self.json(401, {"error": "Unauthenticated"})
        payload = self.request_json()
        try:
            services = validate_services(payload)
            with DB_LOCK:
                save_services(services)
        except (OSError, ValueError) as error:
            return self.json(400, {"error": str(error)})
        return self.json(200, {"services": services})

    def do_GET(self):
        request = urlparse(self.path)
        path = request.path
        if self.path == "/api/auth/session":
            authenticated = self.authenticated()
            return self.json(
                200 if authenticated else 401,
                {"authenticated": authenticated},
            )
        if path == "/api/dashboard":
            if not self.authenticated():
                return self.json(401, {"error": "Unauthenticated"})
            return self.json(200, dashboard())
        if path == "/api/bandwidth":
            if not self.authenticated():
                return self.json(401, {"error": "Unauthenticated"})
            try:
                hours = int(request.query.split("=", 1)[1]) if request.query.startswith("hours=") else 24
            except ValueError:
                return self.json(400, {"error": "Invalid hours"})
            if hours not in {1, 24, 168}:
                return self.json(400, {"error": "Invalid hours"})
            interface = default_interface()
            return self.json(
                200,
                {
                    "hours": hours,
                    "interface": interface,
                    "samples": bandwidth_history(interface, hours),
                },
            )
        if path == "/api/services":
            if not self.authenticated():
                return self.json(401, {"error": "Unauthenticated"})
            return self.json(200, {"services": load_services()})
        if self.path.startswith("/api/"):
            return (
                self.json(401, {"error": "Unauthenticated"})
                if not self.authenticated()
                else self.json(404, {"error": "Not found"})
            )
        if self.path in ("/login.html", "/auth.js"):
            return super().do_GET()
        if not self.authenticated():
            self.send_response(302)
            self.send_header("Location", "/login.html")
            self.end_headers()
            return
        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()


if __name__ == "__main__":
    seed_admin()
    cleanup_expired()
    threading.Thread(target=cleanup_worker, daemon=True).start()
    print(f"HomeOps listening on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), HomeOpsHandler).serve_forever()
