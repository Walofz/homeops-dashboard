const API_BASE = '/api';
const demoData = {
  services: [
    { name:'Home Assistant', host:'home.example.com', icon:'⌂', color:'green', status:'Online', ms:'18 ms' },
    { name:'Immich', host:'photos.example.com', icon:'◉', color:'violet', status:'Online', ms:'22 ms' },
    { name:'Vaultwarden', host:'vault.example.com', icon:'◇', color:'blue', status:'Online', ms:'16 ms' },
    { name:'Uptime Kuma', host:'status.example.com', icon:'⌁', color:'orange', status:'Online', ms:'14 ms' },
    { name:'NAS Admin', host:'192.168.1.20:5000', icon:'▣', color:'gray', status:'Private', ms:'—' }
  ],
  tunnels: [['home-assistant','192.168.1.10:8123','home.example.com','Online','18 ms'],['immich','192.168.1.12:2283','photos.example.com','Online','22 ms'],['vaultwarden','192.168.1.15:8080','vault.example.com','Online','16 ms'],['uptime-kuma','192.168.1.20:3001','status.example.com','Online','14 ms'],['ssh-home','192.168.1.5:22','ssh.example.com:2222','Online','21 ms'],['nas-web','192.168.1.20:5000','nas.example.com','Online','19 ms']],
  network: [['VPS EDGE','Singapore · 103.12.45.67','Caddy + FRPS','99.98% uptime'],['HOME UPLINK','Bangkok · Fiber','FRPC outbound connection','12 ms latency'],['LOCAL NETWORK','192.168.1.0/24','14 devices discovered','0 packet loss']],
  alerts: [['warning','Disk space is reaching 80%','VPS /var · 18 minutes ago','Clear unused Docker images or expand the volume.'],['info','Backup completed successfully','Home NAS · Today, 02:00','Encrypted backup is available in the remote storage target.'],['info','Certificate renewed','Caddy · Yesterday, 03:11','TLS certificate renewed automatically for 6 domains.']]
};
let data = demoData;
let toastTimer;

function render() {
  const services = Array.isArray(data.services) ? data.services : [];
  const tunnels = Array.isArray(data.tunnels) ? data.tunnels : [];
  const network = Array.isArray(data.network) ? data.network : [];
  const alerts = Array.isArray(data.alerts) ? data.alerts : [];
  if (data.bandwidth) { document.querySelector('#downloadRate').textContent = `${data.bandwidth.downloadMbps ?? 0} Mbps`; document.querySelector('#uploadRate').textContent = `${data.bandwidth.uploadMbps ?? 0} Mbps`; }
  document.querySelector('#serviceList').innerHTML = services.slice(0,4).map(s => `<div class="service-row"><span class="service-icon ${s.color || 'gray'}">${s.icon || '◈'}</span><div><b>${s.name}</b><small>${s.host}</small></div><span class="online-dot"></span><small>${s.ms || '—'}</small></div>`).join('');
  document.querySelector('#servicesGrid').innerHTML = services.map(s => `<article class="service-card"><span class="service-icon ${s.color || 'gray'}">${s.icon || '◈'}</span><div><h3>${s.name}</h3><p>${s.host}</p></div><span class="status-pill ${s.status === 'Online' ? '' : 'private'}">${s.status || 'Unknown'}</span><footer>Response time <b>${s.ms || '—'}</b></footer></article>`).join('');
  document.querySelector('#tunnelTable').innerHTML = tunnels.map(t => `<tr><td><b>${t[0]}</b></td><td><code>${t[1]}</code></td><td>${t[2]}</td><td><span class="status-pill">${t[3]}</span></td><td>${t[4]}</td></tr>`).join('');
  document.querySelector('#networkCards').innerHTML = network.map((n,i) => `<article class="network-card"><span class="network-num">0${i+1}</span><span class="online-dot"></span><p class="label">${n[0]}</p><h3>${n[1]}</h3><p>${n[2]}</p><footer>${n[3]}</footer></article>`).join('');
  document.querySelector('#alertFeed').innerHTML = alerts.map(a => `<div class="feed-item ${a[0]}"><span>${a[0] === 'warning' ? '!' : 'i'}</span><div><h3>${a[1]}</h3><p>${a[2]}</p><small>${a[3]}</small></div></div>`).join('');
}

function notify(message) { const el = document.querySelector('#toast'); el.textContent = message; el.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 4200); }
function setMode(mode) { document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('active', b.dataset.mode === mode)); localStorage.setItem('homeops-mode', mode); }
async function useLiveApi() {
  setMode('live');
  try {
    const response = await fetch(`${API_BASE}/dashboard`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.services) || !Array.isArray(payload.tunnels)) throw new Error('invalid response');
    data = { ...demoData, ...payload };
    render();
    notify('Live API connected — data refreshed');
  } catch (_) {
    data = demoData; render(); setMode('demo');
    notify('Live API unavailable — showing demo data');
  }
}
document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => {
  if (button.dataset.mode === 'live') useLiveApi(); else { data = demoData; render(); setMode('demo'); notify('Demo data enabled'); }
}));

function showPage(id) { document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === id)); document.querySelectorAll('.nav a').forEach(a => a.classList.toggle('active', a.dataset.page === id)); document.querySelector('#pageTitle').textContent = document.querySelector(`[data-page="${id}"]`)?.textContent.trim().replace('2','') || id; document.body.classList.remove('nav-open'); window.scrollTo({top:0, behavior:'smooth'}); }
document.querySelectorAll('[data-page]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); showPage(a.dataset.page); history.replaceState(null,'','#'+a.dataset.page); }));
document.querySelector('#menuBtn').onclick = () => document.body.classList.toggle('nav-open');
document.querySelector('#logoutBtn').onclick = async () => { await fetch('/api/auth/logout', { method:'POST' }); location.replace('/login.html'); };
function tick(){ document.querySelector('#clock').textContent = new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Bangkok'}).format(new Date()); }
render(); tick(); setInterval(tick,30000); if(location.hash) showPage(location.hash.slice(1)); if(localStorage.getItem('homeops-mode') === 'live') useLiveApi();
