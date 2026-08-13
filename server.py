#!/usr/bin/env python3
"""Small HomeOps server: static files, SQLite credentials, and signed sessions."""
import base64, hashlib, hmac, json, os, secrets, sqlite3, time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "homeops.db"
PORT = int(os.getenv("HOMEOPS_PORT", "3000"))
USERNAME = os.getenv("HOMEOPS_ADMIN_USER", "admin")
INITIAL_PASSWORD = os.getenv("HOMEOPS_ADMIN_PASSWORD")
SESSION_SECONDS = 60 * 60 * 12

def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at INTEGER NOT NULL)")
    return con

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)

def seed_admin():
    con = db()
    exists = con.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if not exists:
        if not INITIAL_PASSWORD:
            raise RuntimeError("Set HOMEOPS_ADMIN_PASSWORD before first start")
        salt = secrets.token_bytes(16)
        con.execute("INSERT INTO users VALUES (?, ?, ?)", (USERNAME, salt, password_hash(INITIAL_PASSWORD, salt)))
        con.commit()
    con.close()

class HomeOpsHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("HomeOps:", fmt % args)

    def cookie_token(self):
        parts = self.headers.get("Cookie", "").split(";")
        for part in parts:
            key, _, value = part.strip().partition("=")
            if key == "homeops_session": return value
        return None

    def authenticated(self):
        token = self.cookie_token()
        if not token: return False
        con = db(); row = con.execute("SELECT expires_at FROM sessions WHERE token=?", (token,)).fetchone(); con.close()
        return bool(row and row[0] > int(time.time()))

    def json(self, status, value, cookie=None):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/auth/login":
            try:
                size = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(size))
                username, password = payload.get("username", ""), payload.get("password", "")
            except Exception: return self.json(400, {"error":"Invalid request"})
            con = db(); row = con.execute("SELECT salt,password_hash FROM users WHERE username=?", (username,)).fetchone()
            valid = bool(row and hmac.compare_digest(password_hash(password, row[0]), row[1]))
            if not valid: con.close(); return self.json(401, {"error":"Invalid credentials"})
            token = secrets.token_urlsafe(32); expires = int(time.time()) + SESSION_SECONDS
            con.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
            con.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token, username, expires)); con.commit(); con.close()
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return self.json(200, {"ok":True}, f"homeops_session={token}; HttpOnly; SameSite=Strict; Path=/{secure}; Max-Age={SESSION_SECONDS}")
        if self.path == "/api/auth/logout":
            token = self.cookie_token()
            if token:
                con=db(); con.execute("DELETE FROM sessions WHERE token=?", (token,)); con.commit(); con.close()
            return self.json(200, {"ok":True}, "homeops_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
        return self.json(404, {"error":"Not found"})

    def do_GET(self):
        if self.path == "/api/auth/session": return self.json(200 if self.authenticated() else 401, {"authenticated": self.authenticated()})
        if self.path.startswith("/api/"):
            if not self.authenticated(): return self.json(401, {"error":"Unauthenticated"})
            return self.json(404, {"error":"API endpoint not implemented"})
        if self.path in ("/login.html", "/auth.js"):
            return super().do_GET()
        if not self.authenticated():
            self.send_response(302); self.send_header("Location", "/login.html"); self.end_headers(); return
        if self.path == "/": self.path = "/index.html"
        return super().do_GET()

if __name__ == "__main__":
    seed_admin()
    print(f"HomeOps listening on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), HomeOpsHandler).serve_forever()
