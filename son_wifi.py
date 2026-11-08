"""
son_wifi.py - Envoie le son du PC (Windows) vers l'iPhone par le Wi-Fi.

Sélecteur sur l'iPhone : 🐢 Faible / ⚖️ Stable / 💎 HD
  - Faible : économise le Wi-Fi (96 à 192 kb/s)
  - Stable : trouve la meilleure qualité qui ne coupe pas, se verrouille dessus,
             ignore les petites variations du réseau
  - HD     : qualité maximale (768 à 1536 kb/s) avec grosse réserve anti-coupure

Streaming adaptatif (comme YouTube) :
  - 6 niveaux de qualité, de 1536 kb/s (son brut) à 96 kb/s (très compressé)
  - la qualité baisse toute seule si le Wi-Fi rame, et remonte quand c'est stable
  - tampon anti-coupure sur l'iPhone qui s'agrandit/réduit automatiquement
  - les silences ne consomment presque rien

Installation (une seule fois) :
    pip install pyaudiowpatch aiohttp qrcode numpy

Lancer :
    python son_wifi.py
Puis ouvrir dans Safari sur l'iPhone l'adresse affichée (ex: http://192.168.1.20:8000)
et appuyer sur "Écouter".
(Un QR code s'affiche aussi dans le terminal : scanne-le avec l'appareil photo de l'iPhone.)
"""
import asyncio
import json
import socket
import struct
import threading
import time

import numpy as np
import pyaudiowpatch as pyaudio
from aiohttp import web, WSMsgType

PORT = 8000
CHUNK = 1152          # images audio par paquet (~24 ms à 48 kHz), divisible par 1, 2, 3, 4
SILENCE_SEUIL = 1e-4  # en dessous : considéré comme silence
FILE_MAX = 10         # paquets en attente max par appareil (~240 ms) avant de jeter
NIVEAU_DEPART = 2

# Niveaux de qualité : (division de fréquence, canaux, codec)
#   pcm16 = 16 bits brut ; ulaw = 8 bits compressé (mu-law)
NIVEAUX = [
    (1, 2, "pcm16"),  # 0 : qualité max      ~1536 kb/s
    (1, 2, "ulaw"),   # 1 : très bonne        ~768 kb/s
    (2, 2, "ulaw"),   # 2 : bonne (départ)    ~384 kb/s
    (2, 1, "ulaw"),   # 3 : moyenne, mono     ~192 kb/s
    (3, 1, "ulaw"),   # 4 : basse, mono       ~128 kb/s
    (4, 1, "ulaw"),   # 5 : très basse, mono   ~96 kb/s
]

PAGE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Son du PC</title>
<style>
body{font-family:-apple-system,sans-serif;background:#111;color:#eee;text-align:center;padding:40px 20px}
button#b{font-size:26px;padding:20px 44px;border-radius:14px;border:0;background:#2a7;color:#fff}
#s{margin-top:24px;font-size:18px}
#modes{display:flex;max-width:340px;margin:0 auto 28px;background:#222;border-radius:12px;padding:4px;gap:4px}
#modes button{flex:1;font-size:16px;padding:12px 4px;border:0;border-radius:9px;background:transparent;color:#aaa}
#modes button.on{background:#2a7;color:#fff;font-weight:600}
#aide{max-width:340px;margin:-18px auto 18px;font-size:13px;color:#888}
#ecran{display:block;width:100%;max-width:340px;margin:0 auto 28px;font-size:16px;padding:12px;border-radius:12px;border:1px solid #333;background:#222;color:#aaa}
#ecran.on{background:#d9a400;border-color:#d9a400;color:#111;font-weight:600}
#q{margin:22px auto 0;max-width:340px;font-size:15px;color:#aaa;text-align:left;line-height:1.7}
#q b{color:#eee}
.bar{display:flex;gap:4px;margin:6px 0 2px}
.bar i{flex:1;height:10px;border-radius:3px;background:#333}
.bar i.on{background:#2a7}
.bar i.hors{opacity:.35}
</style></head><body>
<h1>🎧 Son du PC</h1>
<div id="modes">
  <button data-m="faible">🐢 Faible</button>
  <button data-m="stable">⚖️ Stable</button>
  <button data-m="hd">💎 HD</button>
</div>
<div id="aide"></div>
<button id="ecran">🌙 Mise en veille : autorisée</button>
<button id="b">▶️ Écouter</button>
<div id="s">Appuie sur Écouter</div>
<div id="q"></div>
<script>
const b = document.getElementById('b'), s = document.getElementById('s'), q = document.getElementById('q');

// --- Les 3 modes ---
//  min/max : niveaux autorisés (0 = meilleure qualité, 5 = plus économe)
const MODES = {
  faible: {min: 3, max: 5, depart: 4, tampon: 0.15, tamponMin: 0.10,
           aide: 'Économise le Wi-Fi (96 à 192 kb/s). Idéal si le réseau est mauvais.'},
  stable: {min: 0, max: 5, depart: 2, tampon: 0.20, tamponMin: 0.10,
           aide: 'Trouve tout seul la meilleure qualité qui ne coupe jamais, puis se verrouille dessus. Ignore les petites variations.'},
  hd:     {min: 0, max: 1, depart: 0, tampon: 0.30, tamponMin: 0.25,
           aide: 'Qualité maximale (768 à 1536 kb/s), avec une plus grande réserve anti-coupure.'},
};
let mode = localStorage.getItem('mode') || 'stable';
if (!MODES[mode]) mode = 'stable';

const TAMPON_MAX = 0.8;
let tampon = MODES[mode].tampon;
let ctx, ws, next = 0, niveaux = [], niveau = 0, rate = 48000;
let dernierPaquet = 0, dureePrec = 0, coupures = 0, octets = 0, kbps = 0;
let derniereCoupure = 0, dernierChangement = 0;
// mode Faible / HD : montée avec temporisation croissante
let attenteMontee = 15, montee = false;
// mode Stable
let echecs = {};            // niveau -> {jusqua, compte} : niveaux qui ont déjà coupé
let recentes = [];          // instants des coupures récentes
let gigue = 0;              // pic de variation d'arrivée des paquets (s), décroît lentement
// Masquage des coupures
const FONDU = 0.025;        // durée des fondus enchaînés (s)
let bus = null;             // volume général courant
let attente = null;         // son de remplacement programmé après le dernier paquet
let hist = [], histRate = 0, masquees = 0;

// Table de décodage mu-law (8 bits -> flottant)
const ULAW = new Float32Array(256);
for (let i = 0; i < 256; i++) {
  const y = i / 255 * 2 - 1;
  ULAW[i] = Math.sign(y) * (Math.pow(256, Math.abs(y)) - 1) / 255;
}

// --- Sélecteur ---
const btns = document.querySelectorAll('#modes button');
btns.forEach(x => x.onclick = () => choisirMode(x.dataset.m));
function majSelecteur() {
  btns.forEach(x => x.classList.toggle('on', x.dataset.m === mode));
  document.getElementById('aide').textContent = MODES[mode].aide;
}
function choisirMode(m) {
  mode = m; localStorage.setItem('mode', m);
  const M = MODES[m];
  tampon = M.tampon; echecs = {}; recentes = []; montee = false; attenteMontee = 15;
  if (ctx) { derniereCoupure = dernierChangement = ctx.currentTime; next = 0; }
  envoyerMode();
  majSelecteur(); afficher();
}
function envoyerMode() {
  const M = MODES[mode];
  envoyer({type: 'mode', nom: mode, min: M.min, max: M.max, niveau: M.depart});
}
majSelecteur();

// --- Anti-veille : empêche l'iPhone de verrouiller l'écran (ce qui couperait le son) ---
// 1) API officielle Wake Lock si dispo (seulement en HTTPS)
// 2) sinon : petite vidéo noire, muette et invisible qui tourne en boucle (méthode NoSleep)
const ecranBtn = document.getElementById('ecran');
let veilleBloquee = false, wakeLock = null, video = null;
async function bloquerVeille() {
  if ('wakeLock' in navigator && window.isSecureContext) {
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
      return true;
    } catch (e) {}
  }
  if (!video) {
    video = document.createElement('video');
    video.setAttribute('playsinline', ''); video.setAttribute('muted', '');
    video.muted = true; video.loop = true;
    video.src = 'data:video/mp4;base64,__VIDEO__';
    video.style.cssText = 'position:fixed;left:0;bottom:0;width:1px;height:1px;opacity:.01;pointer-events:none';
    // certaines versions d'iOS ignorent "loop" : on revient au début nous-mêmes
    video.addEventListener('timeupdate', () => { if (video.currentTime > 1.5) video.currentTime = 0.1; });
    document.body.appendChild(video);
  }
  try { await video.play(); return true; } catch (e) { return false; }
}
function libererVeille() {
  if (wakeLock) { wakeLock.release(); wakeLock = null; }
  if (video) video.pause();
}
async function basculerVeille(etat) {
  veilleBloquee = etat;
  localStorage.setItem('ecran', etat ? '1' : '0');
  if (etat && !(await bloquerVeille())) veilleBloquee = false;
  if (!etat) libererVeille();
  majEcran();
}
function majEcran() {
  ecranBtn.classList.toggle('on', veilleBloquee);
  ecranBtn.textContent = veilleBloquee ? '🔆 Écran toujours allumé' : '🌙 Mise en veille : autorisée';
}
ecranBtn.onclick = () => basculerVeille(!veilleBloquee);
// En revenant sur la page, iOS a relâché le blocage : on le reprend
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && veilleBloquee) bloquerVeille();
});

b.onclick = () => {
  if (navigator.audioSession) navigator.audioSession.type = 'playback';
  if (localStorage.getItem('ecran') === '1') basculerVeille(true);   // choix mémorisé
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  ctx.resume();
  b.style.display = 'none';
  connect();
  setInterval(tick, 1000);
};

function envoyer(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }

function connect() {
  ws = new WebSocket('ws://' + location.host + '/ws');
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => s.textContent = 'Connecté ✅ — lance un son sur le PC';
  ws.onmessage = e => {
    if (typeof e.data === 'string') {
      const m = JSON.parse(e.data);
      if (m.type === 'config') { niveaux = m.niveaux; rate = m.rate; niveau = m.niveau; envoyerMode(); }
      if (m.type === 'niveau') { niveau = m.niveau; dernierChangement = ctx.currentTime; }
      return;
    }
    octets += e.data.byteLength;
    recevoir(e.data);
  };
  ws.onclose = () => { s.textContent = 'Déconnecté… reconnexion'; next = 0; setTimeout(connect, 1000); };
}

function recevoir(buf) {
  const now = ctx.currentTime;
  const ecart = now - dernierPaquet;   // temps depuis le paquet précédent
  dernierPaquet = now;
  const lvl = new Uint8Array(buf, 0, 1)[0];
  if (lvl === 255) { next = 0; dureePrec = 0; return; } // silence
  if (!niveaux[lvl]) return;
  const [div, ch, codec] = niveaux[lvl];
  const r = rate / div;

  // Décodage
  let pcm;
  if (codec === 'pcm16') {
    const v = new Int16Array(buf, 2); // octet 1 = bourrage pour l'alignement
    pcm = new Float32Array(v.length);
    for (let i = 0; i < v.length; i++) pcm[i] = v[i] / 32768;
  } else {
    const v = new Uint8Array(buf, 1);
    pcm = new Float32Array(v.length);
    for (let i = 0; i < v.length; i++) pcm[i] = ULAW[v[i]];
  }
  const frames = Math.floor(pcm.length / ch);
  if (!frames) return;

  // Safari refuse parfois les fréquences < 22050 Hz : on sur-échantillonne (interpolation)
  const up = r < 22050 ? Math.ceil(22050 / r) : 1;
  const ab = ctx.createBuffer(2, frames * up, r * up);
  for (let c = 0; c < 2; c++) {
    const d = ab.getChannelData(c), cc = ch === 1 ? 0 : c;
    for (let i = 0; i < frames; i++) {
      const a = pcm[i * ch + cc];
      const z = i + 1 < frames ? pcm[(i + 1) * ch + cc] : a;
      for (let k = 0; k < up; k++) d[i * up + k] = a + (z - a) * k / up;
    }
  }

  // Mesure de la gigue réseau (écart entre l'arrivée réelle et l'arrivée attendue)
  const ecartAttendu = dureePrec;
  dureePrec = ab.duration;

  // Historique des derniers ~50 ms, pour fabriquer le son de remplacement
  if (histRate !== ab.sampleRate) { hist = []; histRate = ab.sampleRate; }
  hist.push([ab.getChannelData(0), ab.getChannelData(1)]);
  while (hist.length > 1 && (hist.length - 1) * ab.length >= 0.05 * ab.sampleRate) hist.shift();

  // Ordonnancement
  let vitesse = 1;
  if (next === 0 || (next < now + 0.005 && ecart >= 0.4)) {
    // Démarrage (ou reprise après un silence) : fondu d'entrée
    next = now + tampon;
    nouveauBus(now, next);
  } else if (next < now + 0.005) {
    // En retard : le son de remplacement est en train de jouer. On reprend vite,
    // en fondu enchaîné, puis on reconstruit la réserve en ralentissant à peine.
    coupure(now);
    masquees++;
    next = now + Math.max(0.04, tampon * 0.5);
    nouveauBus(now, next);
    attente = null;          // il est sur l'ancien bus, qui s'éteint en fondu
  } else if (next > now + tampon + 0.6) {
    // Énorme retard accumulé (rare) : on saute, en fondu enchaîné
    next = now + tampon;
    nouveauBus(now, next);
    attente = null;
  } else {
    // Cas normal : le paquet arrive à l'heure, on annule le son de remplacement prévu
    if (attente) { try { attente.stop(); } catch (e) {} attente = null; }
    // Gigue mesurée seulement sur les paquets à l'heure : une coupure ne la fausse pas
    if (ecartAttendu) gigue = Math.max(Math.abs(ecart - ecartAttendu), gigue * 0.9995);
    // Rattrapage en douceur (±1,5 %, inaudible) au lieu de couper ou d'insérer du silence
    const avance = next - now;
    if (avance < tampon * 0.85) vitesse = 0.985;
    else if (avance > tampon + 0.15) vitesse = 1.015;
  }
  const src = ctx.createBufferSource();
  src.buffer = ab;
  src.playbackRate.value = vitesse;
  src.connect(bus);
  src.start(next);
  next += ab.duration / vitesse;

  // Son de remplacement prêt à partir juste après ce paquet, au cas où le suivant serait en retard
  const m = ctx.createBufferSource();
  m.buffer = masque();
  m.connect(bus);
  m.start(next);
  attente = m;
}

// Chaque "bus" est un volume général. Pour reprendre sans clic, on crée un nouveau bus
// qui monte en fondu pendant que l'ancien descend : un fondu enchaîné de 25 ms.
function nouveauBus(now, debut) {
  if (bus) {
    const old = bus, v = old.gain.value;
    old.gain.cancelScheduledValues(now);
    old.gain.setValueAtTime(v, now);
    old.gain.setValueAtTime(v, debut);
    old.gain.linearRampToValueAtTime(0, debut + FONDU);
    setTimeout(() => old.disconnect(), (debut - now + FONDU) * 1000 + 300);
  }
  bus = ctx.createGain();
  bus.gain.setValueAtTime(0, now);
  bus.gain.setValueAtTime(0, debut);
  bus.gain.linearRampToValueAtTime(1, debut + FONDU);
  bus.connect(ctx.destination);
}

// Son de remplacement (~250 ms) : les derniers 50 ms joués en miroir
// (à l'envers, à l'endroit, à l'envers…) pour rester continu, avec un fondu de sortie.
// Il commence exactement sur le dernier échantillon joué : pas de clic.
function masque() {
  const n = hist.reduce((a, x) => a + x[0].length, 0);
  const L = n * 5, out = ctx.createBuffer(2, L, histRate);
  for (let c = 0; c < 2; c++) {
    const h = new Float32Array(n);
    let o = 0;
    for (const x of hist) { h.set(x[c], o); o += x[c].length; }
    const d = out.getChannelData(c);
    for (let i = 0; i < L; i++) {
      const j = i % n, v = Math.floor(i / n) % 2 ? h[j] : h[n - 1 - j];
      d[i] = v * 0.5 * (1 + Math.cos(Math.PI * i / L));
    }
  }
  return out;
}

function coupure(now) {
  coupures++;
  derniereCoupure = now;
  const M = MODES[mode];
  tampon = Math.min(TAMPON_MAX, tampon + 0.05);       // plus de marge, dans tous les modes

  if (mode === 'stable') {
    // Une coupure isolée = mini variation : on agrandit juste le tampon.
    // Deux coupures en 20 s = ce niveau n'est vraiment pas tenable.
    recentes = recentes.filter(t => now - t < 20).concat(now);
    if (recentes.length >= 2) {
      recentes = [];
      const e = echecs[niveau] || {compte: 0};
      e.compte++;
      e.jusqua = now + Math.min(1800, 120 * Math.pow(2, e.compte - 1)); // 2 min, 4, 8… max 30 min
      echecs[niveau] = e;
      if (niveau < M.max) envoyer({type: 'baisser'});
    }
    return;
  }

  // Faible / HD : réaction immédiate
  if (montee && now - dernierChangement < 10) attenteMontee = Math.min(120, attenteMontee * 2);
  montee = false;
  if (niveau < M.max) envoyer({type: 'baisser'});
}

// En mode Stable : le niveau au-dessus a-t-il déjà coupé récemment ?
function verrouille(now) {
  if (niveau <= MODES[mode].min) return true;
  const e = echecs[niveau - 1];
  return !!(e && e.jusqua > now);
}

function tick() {
  const now = ctx.currentTime;
  const M = MODES[mode];
  kbps = Math.round(octets * 8 / 1000); octets = 0;
  const calme = now - Math.max(derniereCoupure, dernierChangement);

  if (mode === 'stable') {
    // Tampon calculé à partir de la gigue réelle, avec une zone morte de ±25 %
    const cible = Math.min(TAMPON_MAX, Math.max(M.tamponMin, gigue * 3 + 0.05));
    if (cible > tampon * 1.25) tampon = cible;                       // le réseau se dégrade : on monte direct
    else if (cible < tampon * 0.75 && now - derniereCoupure > 30)
      tampon = Math.max(cible, tampon - 0.005);                       // il s'améliore : on descend très doucement
    // Montée prudente : 45 s sans coupure, et seulement vers un niveau qui n'a pas déjà échoué
    if (calme > 45 && !verrouille(now)) {
      dernierChangement = now;
      envoyer({type: 'monter'});
    }
  } else {
    if (now - derniereCoupure > 20 && tampon > M.tamponMin) tampon = Math.max(M.tamponMin, tampon - 0.01);
    if (niveau > M.min && calme > attenteMontee) {
      montee = true;
      dernierChangement = now;
      envoyer({type: 'monter'});
    }
    if (montee && now - dernierChangement > 10) { montee = false; attenteMontee = Math.max(15, attenteMontee * 0.75); }
  }
  afficher();
}

function afficher() {
  if (!niveaux.length || !ctx) return;
  const M = MODES[mode], n = niveaux.length;
  const barres = Array.from({length: n}, (_, i) => {
    const lv = n - 1 - i;   // barre i (gauche = économe) -> niveau
    return '<i class="' + (lv >= niveau ? 'on' : '') + (lv < M.min || lv > M.max ? ' hors' : '') + '"></i>';
  }).join('');
  const [div, ch] = niveaux[niveau];
  let etat = '';
  if (mode === 'stable') etat = verrouille(ctx.currentTime) ? ' · 🔒 verrouillé' : ' · 🔎 recherche…';
  q.innerHTML = 'Qualité <b>' + (n - niveau) + '/' + n + '</b> — ' +
    Math.round(rate / div / 100) / 10 + ' kHz ' + (ch === 2 ? 'stéréo' : 'mono') + etat +
    '<div class="bar">' + barres + '</div>' +
    'Débit : <b>' + kbps + ' kb/s</b><br>' +
    'Tampon : <b>' + Math.round(tampon * 1000) + ' ms</b> · Gigue : <b>' + Math.round(gigue * 1000) + ' ms</b><br>' +
    'Coupures masquées : <b>' + masquees + '</b>';
}
</script></body></html>"""

# Vidéo noire de 2 s (16x16, 2,5 Ko) pour empêcher la mise en veille de l'iPhone
VIDEO_VEILLE = "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"
PAGE = PAGE.replace("__VIDEO__", VIDEO_VEILLE)


# ---------------------------------------------------------------- Encodage

def encoder(x, niveau):
    """x : float32 (frames, 2) dans [-1, 1]  ->  bytes pour ce niveau."""
    div, ch, codec = NIVEAUX[niveau]
    if div > 1:
        x = x[: len(x) // div * div].reshape(-1, div, 2).mean(axis=1)  # filtre + sous-échantillonnage
    if ch == 1:
        x = x.mean(axis=1, keepdims=True)
    x = np.clip(x, -1.0, 1.0).ravel()
    if codec == "pcm16":
        return struct.pack("BB", niveau, 0) + (x * 32767).astype("<i2").tobytes()
    y = np.sign(x) * np.log1p(255 * np.abs(x)) / np.log(256)  # mu-law
    return struct.pack("B", niveau) + np.round((y + 1) * 127.5).astype(np.uint8).tobytes()


SILENCE = struct.pack("B", 255)


def journal(msg):
    """Affiche un message. L'application Windows remplace cette fonction."""
    print(msg, flush=True)


class Client:
    def __init__(self, ws, remote):
        self.ws = ws
        self.remote = remote
        self.niveau = NIVEAU_DEPART
        self.min, self.max = 0, len(NIVEAUX) - 1   # bornes imposées par le mode choisi
        self.file = asyncio.Queue(maxsize=FILE_MAX)
        self.pertes = []          # instants des paquets jetés
        self.dernier_changement = 0.0
        self.en_silence = False

    def changer(self, delta, raison):
        self.aller(self.niveau + delta, raison)

    def aller(self, n, raison):
        n = max(self.min, min(self.max, n))
        if n == self.niveau:
            return
        self.niveau = n
        self.dernier_changement = time.monotonic()
        div, ch, _ = NIVEAUX[n]
        journal(f"📶 {self.remote} : qualité {len(NIVEAUX) - n}/{len(NIVEAUX)} ({raison})")
        asyncio.ensure_future(self.ws.send_str(json.dumps({"type": "niveau", "niveau": n})))

    def pousser(self, paquet):
        if self.file.full():
            # Le Wi-Fi ne suit pas : on jette le plus vieux paquet (pas d'accumulation de retard)
            try:
                self.file.get_nowait()
            except asyncio.QueueEmpty:
                pass
            t = time.monotonic()
            self.pertes = [p for p in self.pertes if t - p < 2] + [t]
            if len(self.pertes) >= 3 and t - self.dernier_changement > 1.5:
                self.pertes.clear()
                self.changer(+1, "réseau saturé")
        self.file.put_nowait(paquet)

    async def envoyeur(self):
        while not self.ws.closed:
            paquet = await self.file.get()
            try:
                await self.ws.send_bytes(paquet)
            except (ConnectionError, RuntimeError):
                break


# ---------------------------------------------------------------- Outils

def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def afficher_qr(url):
    """Affiche un QR code de l'adresse directement dans le terminal."""
    try:
        import qrcode
    except ImportError:
        print("   (Pour voir un QR code ici : pip install qrcode)")
        return
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    print()
    print("📷 Scanne ce QR code avec l'appareil photo de l'iPhone :")
    qr.print_ascii(invert=True)
    print()


def qr_png(url, couleur="#0B1220", fond="white"):
    """QR code de l'adresse en image PNG (bytes), pour l'application."""
    import io
    import qrcode
    qr = qrcode.QRCode(border=2, box_size=12, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color=couleur, back_color=fond).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- Serveur

class Serveur:
    """Serveur audio pilotable : demarrer() / arreter().

    Tourne dans son propre fil (thread) avec sa boucle asyncio, ce qui permet
    de l'utiliser depuis une interface graphique.
    """

    def __init__(self, port=PORT, quand_change=None):
        self.port = port
        self.quand_change = quand_change   # appelé quand un appareil se connecte/déconnecte
        self.clients = set()
        self.url = None
        self.appareil = None
        self.en_marche = False
        self._fil = None
        self._boucle = None
        self._stop = None
        self._rate = 48000
        self._pret = threading.Event()
        self._erreur = None

    # --- API publique ---------------------------------------------------
    def demarrer(self, delai=10):
        if self.en_marche:
            return self.url
        self._pret.clear()
        self._erreur = None
        self._fil = threading.Thread(target=lambda: asyncio.run(self._principal()), daemon=True)
        self._fil.start()
        if not self._pret.wait(delai):
            raise RuntimeError("Le serveur ne répond pas.")
        if self._erreur:
            raise RuntimeError(self._erreur)
        return self.url

    def arreter(self):
        if self._boucle and self._stop:
            self._boucle.call_soon_threadsafe(self._stop.set)
        if self._fil:
            self._fil.join(5)
        self._fil = None

    @property
    def nb_appareils(self):
        return len(self.clients)

    # --- Interne --------------------------------------------------------
    def _signaler(self):
        if self.quand_change:
            try:
                self.quand_change()
            except Exception:
                pass

    def _diffuser(self, bloc):
        """Appelé dans la boucle asyncio pour chaque bloc audio capturé."""
        silence = float(np.abs(bloc).max()) < SILENCE_SEUIL
        cache = {}
        for c in list(self.clients):
            if c.ws.closed:
                self.clients.discard(c)
                continue
            if silence:
                if not c.en_silence:
                    c.en_silence = True
                    c.pousser(SILENCE)
                continue
            c.en_silence = False
            if c.niveau not in cache:
                cache[c.niveau] = encoder(bloc, c.niveau)
            c.pousser(cache[c.niveau])

    async def _index(self, request):
        return web.Response(text=PAGE, content_type="text/html")

    async def _ws(self, request):
        ws = web.WebSocketResponse(heartbeat=5, max_msg_size=4096)
        await ws.prepare(request)
        c = Client(ws, request.remote)
        await ws.send_str(json.dumps({
            "type": "config", "rate": self._rate,
            "niveau": c.niveau, "niveaux": NIVEAUX,
        }))
        self.clients.add(c)
        tache = asyncio.ensure_future(c.envoyeur())
        journal(f"📱 Appareil connecté : {request.remote}")
        self._signaler()
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                if m.get("type") == "baisser":
                    c.changer(+1, "coupure côté iPhone")
                elif m.get("type") == "monter":
                    c.changer(-1, "connexion stable")
                elif m.get("type") == "mode":
                    try:
                        c.min = max(0, min(len(NIVEAUX) - 1, int(m["min"])))
                        c.max = max(c.min, min(len(NIVEAUX) - 1, int(m["max"])))
                        depart = int(m["niveau"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    journal(f"🎛️ {c.remote} : mode {str(m.get('nom', '?'))[:10]}")
                    c.niveau = -1  # force l'envoi du niveau à l'iPhone
                    c.aller(depart, "changement de mode")
        finally:
            self.clients.discard(c)
            tache.cancel()
            journal(f"📴 Appareil déconnecté : {request.remote}")
            self._signaler()
        return ws

    async def _principal(self):
        self._boucle = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        p = stream = runner = None
        try:
            p = pyaudio.PyAudio()
            try:
                dev = p.get_default_wasapi_loopback()
            except Exception:
                raise RuntimeError("Aucune sortie audio trouvée (haut-parleurs / casque).")
            self._rate = rate = int(dev["defaultSampleRate"])
            ch = int(dev["maxInputChannels"])
            self.appareil = dev["name"].replace(" [Loopback]", "")

            app = web.Application()
            app.router.add_get("/", self._index)
            app.router.add_get("/ws", self._ws)
            runner = web.AppRunner(app)
            await runner.setup()
            try:
                await web.TCPSite(runner, "0.0.0.0", self.port).start()
            except OSError:
                raise RuntimeError(f"Le port {self.port} est déjà utilisé : "
                                   "Son Wi-Fi tourne sûrement déjà ailleurs.")

            boucle = self._boucle

            def rappel(data, n, info, statut):   # appelé par la carte son, dans son propre fil
                x = np.frombuffer(data, dtype="<i2").reshape(-1, ch).astype(np.float32) / 32768
                if ch == 1:
                    x = np.repeat(x, 2, axis=1)
                elif ch > 2:
                    x = x[:, :2]
                boucle.call_soon_threadsafe(self._diffuser, x)
                return (None, pyaudio.paContinue)

            stream = p.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
                            input_device_index=dev["index"], frames_per_buffer=CHUNK,
                            stream_callback=rappel)
            stream.start_stream()

            self.url = f"http://{local_ip()}:{self.port}"
            self.en_marche = True
            journal(f"🔊 Son capturé depuis : {self.appareil} ({rate} Hz, {ch} canaux)")
            journal(f"👉 Adresse : {self.url}")
            self._pret.set()
            await self._stop.wait()
        except Exception as e:
            self._erreur = str(e) or e.__class__.__name__
            self._pret.set()
        finally:
            self.en_marche = False
            for c in list(self.clients):
                try:
                    await c.ws.close()
                except Exception:
                    pass
            self.clients.clear()
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if runner is not None:
                await runner.cleanup()
            if p is not None:
                p.terminate()
            if self._erreur is None:
                journal("⏹️ Serveur arrêté")
            self._signaler()


# ---------------------------------------------------------------- Mode terminal

def main():
    serveur = Serveur()
    try:
        url = serveur.demarrer()
    except RuntimeError as e:
        print("❌", e)
        return
    print(f"🎚️  Streaming adaptatif : {len(NIVEAUX)} niveaux")
    print(f"👉 Sur l'iPhone, ouvre Safari et va sur :  {url}")
    afficher_qr(url)
    print("   (Ctrl+C pour arrêter)")
    try:
        while serveur.en_marche:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    serveur.arreter()
    print("Arrêté.")


if __name__ == "__main__":
    main()
