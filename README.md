# HomeOps

HomeOps เป็น dashboard สำหรับโครงสร้าง `Caddy → FRPS → FRPC → services` พร้อมหน้า login และ session cookie ของตัวเอง ไม่ใช้ Caddy Basic Auth และไม่มี WireGuard

## สิ่งที่โปรเจกต์ทำได้ตอนนี้

- หน้า login แบบ custom ที่ใช้ username/password จริง
- เก็บ password hash และ session ใน SQLite (`homeops.db`)
- ป้องกันหน้า dashboard และ `/api/*` ที่ server ก่อนส่งข้อมูล
- มี Demo / Live API switch; Live เรียก `GET /api/dashboard` ที่มีใน `server.py`
- Live เก็บ bandwidth ของ VPS, ตรวจ FRPS metrics และ health-check services จริง

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
Environment=HOMEOPS_FRPS_METRICS_URL=http://127.0.0.1:7500/metrics
Environment=HOMEOPS_FRPS_USER=homeops
Environment=HOMEOPS_FRPS_PASSWORD=เปลี่ยนเป็นรหัสผ่าน FRPS dashboard
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

สร้างไฟล์ตั้งค่าบริการจริงก่อนเปิด Live mode:

```sh
cp /opt/homeops/services.example.json /opt/homeops/services.json
sudo chown homeops:homeops /opt/homeops/services.json
```

แก้ `services.json` ให้เป็นโดเมน, health URL และ IP:port ภายในบ้านของคุณ. ไฟล์นี้ถูก ignore จาก Git เพื่อไม่เผยข้อมูล LAN ของคุณ หลังล็อกอินแล้วสามารถกด **Manage services** ในหน้า **Services** เพื่อเพิ่ม, แก้ไข หรือลบรายการได้; การเปลี่ยนแปลงนี้มีผลเฉพาะ HomeOps monitoring และไม่แก้ไข `frpc.toml`

## Caddy

เปลี่ยนโดเมนใน [Caddyfile.example](Caddyfile.example) แล้ววาง block นั้นใน Caddyfile ของ VPS:

```caddyfile
dashboard.example.com {
    reverse_proxy 127.0.0.1:13000
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

## ความปลอดภัยในการใช้งาน

- HomeOps ออก session cookie พร้อม `Secure`, `HttpOnly` และ `SameSite=Strict` ดังนั้นต้องเข้าผ่าน HTTPS ที่ Caddy เท่านั้น
- ระบบจำกัดความพยายามลงชื่อเข้าใช้ที่ 5 ครั้งต่อบัญชีใน 15 นาที และล้าง session ที่หมดอายุทุกชั่วโมง
- Caddy เป็น trusted reverse proxy ของแอป; อย่า expose port `13000` ออก internet
- Live health checks ทำงานพร้อมกันและ cache ผล 20 วินาที เพื่อลดการรอเมื่อปลายทางหลายรายการล่ม

## Live API

กด **Live API** ในมุมขวาบนเพื่อให้หน้าเว็บเรียก `GET /api/dashboard`. Server จะตอบข้อมูลจริงที่รวม bandwidth ของ VPS, ความพร้อมของ FRPS metrics และ health check ของทุก service ใน `services.json`. ต้องรอสองครั้งในการ refresh เพื่อคำนวณอัตรา bandwidth จากผลต่างของ byte counter

## Live monitoring บน Ubuntu VPS

ส่วนนี้เป็นการตั้งค่าเพิ่มเติมเพื่อใช้โหมด **Live API**; ไม่จำเป็นสำหรับโหมด Demo

### VPS bandwidth

HomeOps backend สามารถอ่าน byte counter ของ network interface จาก `/proc/net/dev` บน Ubuntu ได้โดยตรง แล้วคำนวณอัตรา upload/download จากค่าปัจจุบันเทียบกับ sample ก่อนหน้า. วิธีนี้ไม่ต้องติดตั้ง agent เพิ่ม แต่ history และกราฟย้อนหลังต้องให้ backend บันทึก sample ลง SQLite เอง

### FRP tunnel status

FRPS ที่ไม่ได้เปิด web server ไม่มี status API ที่แนะนำให้ใช้สำหรับ dashboard. เปิด FRPS dashboard และ Prometheus metrics เฉพาะ localhost บน VPS เพื่อให้ HomeOps backend อ่านได้ โดยไม่เปิดพอร์ตออก internet:

```toml
# เพิ่มใน /etc/frp/frps.toml (ตำแหน่งไฟล์อาจต่างกันตามการติดตั้ง)
webServer.addr = "127.0.0.1"
webServer.port = 7500
webServer.user = "homeops"
webServer.password = "ใช้รหัสผ่านยาวและสุ่ม"
enablePrometheus = true
```

ตรวจ config และ restart:

```sh
sudo frps verify -c /etc/frp/frps.toml
sudo systemctl restart frps
curl -u homeops 'http://127.0.0.1:7500/metrics'
```

อย่าเปิด port `7500` ที่ firewall และอย่าให้ Caddy reverse proxy ไปยัง port นี้. HomeOps backend บนเครื่องเดียวกันจึงจะเรียก `http://127.0.0.1:7500/metrics` ได้. FRP ใช้ metrics ร่วมกับ web server/dashboard ดังนั้นต้องเปิดทั้งสองค่านี้จึงจะมี Prometheus endpoint. ดู [FRP monitoring documentation](https://gofrp.org/en/docs/features/common/monitor/) สำหรับข้อมูลเพิ่มเติม

### Service health

backend สามารถตรวจ HTTPS ของโดเมนบริการผ่าน Caddy ตามรายการที่กำหนด แล้วรวมผลเป็นสถานะ Online/Offline. การตรวจแบบนี้เห็นผลจากเส้นทางจริง `Caddy → FRPS → FRPC → service` แต่ไม่ควรใช้แทน authentication หรือการตรวจสอบภายในของแต่ละแอป
