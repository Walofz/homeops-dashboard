# HomeOps

HomeOps เป็น dashboard สำหรับโครงสร้าง `Caddy → FRPS → FRPC → services` พร้อมหน้า login และ session cookie ของตัวเอง ไม่ใช้ Caddy Basic Auth และไม่มี WireGuard

## สิ่งที่โปรเจกต์ทำได้ตอนนี้

- หน้า login แบบ custom ที่ใช้ username/password จริง
- เก็บ password hash และ session ใน SQLite (`homeops.db`)
- ป้องกันหน้า dashboard และ `/api/*` ที่ server ก่อนส่งข้อมูล
- มี Demo / Live API switch; Live เรียก `GET /api/dashboard` เมื่อคุณเพิ่ม endpoint นี้ภายหลัง
- ไม่มีการดึงสถานะ Caddy, FRP หรือ bandwidth จริงในตอนนี้

## Deploy บน VPS

คัดลอกไฟล์โปรเจกต์ทั้งหมดไปที่ `/opt/homeops` และติดตั้ง Python 3 (standard library เพียงพอ ไม่ต้อง `pip install`)

สร้าง systemd service ที่ `/etc/systemd/system/homeops.service`:

```ini
[Unit]
Description=HomeOps dashboard
After=network.target

[Service]
User=homeops
WorkingDirectory=/opt/homeops
Environment=HOMEOPS_ADMIN_USER=admin
Environment=HOMEOPS_ADMIN_PASSWORD=เปลี่ยนเป็นรหัสผ่านยาวและสุ่ม
ExecStart=/usr/bin/python3 /opt/homeops/server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

สร้าง user `homeops` แล้วเปิด service:

```sh
sudo useradd --system --home /opt/homeops --shell /usr/sbin/nologin homeops
sudo chown -R homeops:homeops /opt/homeops
sudo systemctl daemon-reload
sudo systemctl enable --now homeops
```

หลังเริ่มสำเร็จครั้งแรก ให้ย้าย `HOMEOPS_ADMIN_PASSWORD` ออกจาก service file ไปไว้ใน systemd environment file ที่สิทธิ์ `600` ตามนโยบายของคุณ; password จะถูก hash ลง SQLite แล้ว

## Caddy

เปลี่ยนโดเมนใน [Caddyfile.example](Caddyfile.example) แล้ววาง block นั้นใน Caddyfile ของ VPS:

```caddyfile
dashboard.example.com {
    reverse_proxy 127.0.0.1:3000
}
```

ตรวจและ reload:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy ทำ TLS/reverse proxy เท่านั้น; application จะตรวจ login เอง. DNS ของโดเมนต้องชี้มาที่ VPS และเปิด TCP 80/443

## Login

เปิด `https://dashboard.example.com` แล้วลงชื่อด้วย `HOMEOPS_ADMIN_USER` และ `HOMEOPS_ADMIN_PASSWORD` ที่ตั้งตอนเริ่มระบบครั้งแรก

หากเปิด `index.html` ด้วย file browser โดยตรง จะข้ามระบบ login ได้ เพราะไม่ได้ผ่าน `server.py`; สำหรับใช้งานจริงให้เข้าเฉพาะผ่านโดเมน/Caddy

## Live API ในอนาคต

โหมด Live ต้องการ `GET /api/dashboard` ที่ตอบ JSON. Server ปัจจุบันป้องกันเส้นทาง `/api/*` ด้วย session แล้ว แต่ยังไม่ได้สร้างข้อมูล monitoring ให้เอง. เมื่อเพิ่ม endpoint ต้องให้มันคืนอย่างน้อย `services` และ `tunnels`; ดูรูปแบบตัวอย่างใน `demoData` ภายใน [app.js](app.js)
