import os, time, requests, pandas as pd, threading
from datetime import datetime
from flask import Flask, jsonify, render_template_string
import yfinance as yf

API_KEY    = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
BASE_URL   = "https://paper-api.alpaca.markets"

CAPITAL     = 200.0
STOP_LOSS   = 0.015
TAKE_PROFIT = 0.030
EMA_FAST    = 9
EMA_SLOW    = 21
RSI_PERIOD  = 14
RSI_MAX     = 70
INTERVALO   = 60

ACTIVOS = [
    # 🪙 CRYPTO
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
    "ADA-USD", "DOGE-USD", "AVAX-USD", "DOT-USD", "MATIC-USD",
    "LINK-USD", "UNI-USD", "LTC-USD", "ATOM-USD", "NEAR-USD",
    # 📈 ACCIONES USA
    "AAPL",  "TSLA",  "MSFT",  "GOOGL", "AMZN",
    "NVDA",  "META",  "NFLX",  "AMD",   "INTC",
    "BABA",  "UBER",  "PYPL",  "SQ",    "SHOP",
    "KO",    "PEP",   "MCD",   "DIS",   "NKE",
    "JPM",   "BAC",   "GS",    "V",     "MA",
    "PFE",   "JNJ",   "MRNA",  "ABBV",  "CVS",
    "XOM",   "CVX",   "BP",    "COP",   "SLB",
    # 🥇 MATERIAS PRIMAS (ETFs)
    "GLD",   "SLV",   "USO",   "UNG",   "PDBC",
    "IAU",   "SIVR",  "DBO",   "UCO",   "CORN",
    "WEAT",  "SOYB",  "CPER",  "PALL",  "PPLT",
    # 🌍 ETFs GLOBALES
    "SPY",   "QQQ",   "DIA",   "IWM",   "VTI",
    "EEM",   "EFA",   "VEA",   "GDX",   "ARKK",
    # 🏠 OTROS
    "VNQ",   "O",     "AMT",   "PLD",   "REIT",
]

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
}

estado = {
    "activos": {}, "señales": [], "operaciones": [],
    "capital": CAPITAL, "pnl": 0.0, "trades": 0,
    "log": [], "ultimo_update": "—"
}

def log(tipo, msg):
    hora = datetime.now().strftime("%H:%M:%S")
    iconos = {"buy":"🟢","sell":"🔴","wait":"⏳","info":"📊","error":"❌","warn":"⚠️"}
    print(f"{hora}  {iconos.get(tipo,'•')}  {msg}", flush=True)
    estado["log"].insert(0, {"time": hora, "tipo": tipo, "msg": msg})
    if len(estado["log"]) > 100:
        estado["log"].pop()

def alpaca_get(path):
    r = requests.get(f"{BASE_URL}/v2/{path}", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def alpaca_post(path, data):
    r = requests.post(f"{BASE_URL}/v2/{path}", headers=HEADERS, json=data, timeout=10)
    return r.json()

def get_bars(ticker, limit=60):
    try:
        df = yf.Ticker(ticker).history(period="2d", interval="1m")
        if df is None or len(df) < 22:
            return None
        df = df.tail(limit).copy()
        df["c"] = df["Close"].astype(float)
        return df.reset_index()
    except:
        return None

def calc_ema(series, period):
    k = 2 / (period + 1)
    result = [series.iloc[0]]
    for v in series.iloc[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def calc_rsi(series, period=14):
    deltas = series.diff().dropna()
    ag = deltas.clip(lower=0).rolling(period).mean().iloc[-1]
    al = (-deltas).clip(lower=0).rolling(period).mean().iloc[-1]
    return round(100 if al == 0 else 100 - (100 / (1 + ag / al)), 1)

def alpaca_symbol(ticker):
    if "-USD" in ticker:
        return ticker.replace("-USD", "/USD")
    return ticker

def get_position(ticker):
    try:
        sym = alpaca_symbol(ticker).replace("/", "")
        return alpaca_get(f"positions/{sym}")
    except:
        return None

def analizar(ticker):
    df = get_bars(ticker)
    if df is None:
        return None
    close = df["c"]
    e9    = calc_ema(close, EMA_FAST)
    e21   = calc_ema(close, EMA_SLOW)
    rsi   = calc_rsi(close, RSI_PERIOD)
    precio = round(float(close.iloc[-1]), 4)
    e9v, e21v = round(e9[-1], 4), round(e21[-1], 4)
    e9p, e21p = e9[-2], e21[-2]
    cruce_alc = e9p <= e21p and e9v > e21v
    cruce_baj = e9p >= e21p and e9v < e21v
    señal = "COMPRAR" if cruce_alc and rsi < RSI_MAX else "VENDER" if cruce_baj else "ESPERAR"
    return {
        "ticker": ticker, "precio": precio,
        "ema9": e9v, "ema21": e21v, "rsi": rsi,
        "señal": señal, "update": datetime.now().strftime("%H:%M:%S")
    }

def run_bot():
    log("info", f"🤖 Bot iniciado — {len(ACTIVOS)} activos")
    log("info", f"Capital: ${CAPITAL} | SL:{STOP_LOSS*100}% TP:{TAKE_PROFIT*100}%")
    while True:
        try:
            cuenta = alpaca_get("account")
            equity = round(float(cuenta.get("equity", CAPITAL)), 2)
            estado["capital"] = equity
            estado["pnl"]     = round(equity - CAPITAL, 2)
            estado["ultimo_update"] = datetime.now().strftime("%H:%M:%S")
            señales_activas = []

            for ticker in ACTIVOS:
                try:
                    datos = analizar(ticker)
                    if datos is None:
                        continue
                    estado["activos"][ticker] = datos
                    pos    = get_position(ticker)
                    sym    = alpaca_symbol(ticker)
                    precio = datos["precio"]
                    señal  = datos["señal"]

                    if señal == "COMPRAR" and pos is None:
                        es_crypto = "-USD" in ticker
                        qty = round(CAPITAL / len(ACTIVOS) / precio, 6) if es_crypto else max(1, int(CAPITAL / len(ACTIVOS) / precio))
                        if qty > 0:
                            try:
                                alpaca_post("orders", {
                                    "symbol": sym, "qty": str(qty),
                                    "side": "buy", "type": "market",
                                    "time_in_force": "gtc"
                                })
                                estado["trades"] += 1
                                log("buy", f"COMPRA {ticker} @ ${precio:,}")
                                estado["operaciones"].insert(0, {
                                    "time": datetime.now().strftime("%H:%M:%S"),
                                    "tipo": "COMPRA", "ticker": ticker,
                                    "precio": precio, "qty": qty
                                })
                            except Exception as e:
                                log("warn", f"{ticker}: {str(e)[:50]}")

                    elif señal == "VENDER" and pos is not None:
                        qty = pos.get("qty")
                        try:
                            alpaca_post("orders", {
                                "symbol": sym, "qty": str(qty),
                                "side": "sell", "type": "market",
                                "time_in_force": "gtc"
                            })
                            estado["trades"] += 1
                            log("sell", f"VENTA {ticker} @ ${precio:,}")
                            estado["operaciones"].insert(0, {
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "tipo": "VENTA", "ticker": ticker,
                                "precio": precio, "qty": qty
                            })
                        except Exception as e:
                            log("warn", f"{ticker}: {str(e)[:50]}")

                    if señal == "COMPRAR":
                        señales_activas.append(datos)

                except Exception as e:
                    log("error", f"{ticker}: {str(e)[:50]}")
                    continue

            estado["señales"] = señales_activas
            log("info", f"✅ Ciclo completo — {len(señales_activas)} señales activas")

        except Exception as e:
            log("error", f"Error general: {str(e)[:80]}")

        time.sleep(INTERVALO)

DASHBOARD = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Trading Bot Pro</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
  :root{--bg:#050a0e;--s:#0c1318;--b:#1a2530;--g:#00ff9d;--r:#ff3d6b;--y:#ffd166;--bl:#38bdf8;--t:#c8d8e4;--d:#4a6070}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--t);font-family:'Space Mono',monospace;padding:14px;min-height:100vh}
  body::before{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.07) 2px,rgba(0,0,0,.07) 4px);pointer-events:none;z-index:999}
  h1{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;color:var(--g)}
  .header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--b)}
  .pill{display:flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--g);font-size:9px;color:var(--g)}
  .dot{width:6px;height:6px;border-radius:50%;background:var(--g);animation:p 1.4s infinite}
  @keyframes p{0%,100%{opacity:1;box-shadow:0 0 5px var(--g)}50%{opacity:.2}}
  .metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:12px}
  .m{background:var(--s);border:1px solid var(--b);padding:10px}
  .ml{font-size:8px;letter-spacing:.12em;text-transform:uppercase;color:var(--d);margin-bottom:4px}
  .mv{font-family:'Syne',sans-serif;font-size:20px;font-weight:800}
  .mv.g{color:var(--g)} .mv.r{color:var(--r)} .mv.b{color:var(--bl)} .mv.y{color:var(--y)}
  .ms{font-size:8px;color:var(--d);margin-top:2px}
  .tabs{display:flex;gap:0;margin-bottom:12px;border-bottom:1px solid var(--b)}
  .tab{padding:8px 14px;font-size:10px;cursor:pointer;color:var(--d);letter-spacing:.08em;border-bottom:2px solid transparent;transition:all .2s}
  .tab.active{color:var(--g);border-bottom-color:var(--g)}
  .panel{display:none} .panel.active{display:block}
  .table{width:100%;border-collapse:collapse;font-size:10px}
  .table th{text-align:left;padding:6px 8px;color:var(--d);font-size:8px;letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid var(--b)}
  .table td{padding:6px 8px;border-bottom:1px solid rgba(26,37,48,.4)}
  .table tr:hover td{background:rgba(255,255,255,.02)}
  .badge{display:inline-block;padding:2px 7px;font-size:8px;font-weight:700;letter-spacing:.08em}
  .badge.buy{background:rgba(0,255,157,.15);color:var(--g)}
  .badge.sell{background:rgba(255,61,107,.15);color:var(--r)}
  .badge.wait{background:rgba(255,209,102,.1);color:var(--y)}
  .signals-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
  .sig-card{background:var(--s);border:1px solid var(--g);padding:10px}
  .sig-ticker{font-family:'Syne',sans-serif;font-size:16px;font-weight:800;color:var(--g)}
  .sig-price{font-size:11px;color:var(--bl);margin-top:2px}
  .sig-info{font-size:9px;color:var(--d);margin-top:4px}
  .log-box{background:var(--s);border:1px solid var(--b);padding:12px;max-height:220px;overflow-y:auto}
  .log-box::-webkit-scrollbar{width:2px}
  .log-box::-webkit-scrollbar-thumb{background:var(--b)}
  .le{display:flex;gap:8px;font-size:10px;padding:4px 0;border-bottom:1px solid rgba(26,37,48,.4);line-height:1.5}
  .lt{color:var(--d);flex-shrink:0}
  .lbuy{color:var(--g)} .lsell{color:var(--r)} .linfo{color:var(--t)} .lwait{color:var(--y)} .lerror{color:var(--r)}
  .search{width:100%;background:var(--s);border:1px solid var(--b);color:var(--t);padding:8px 12px;font-family:'Space Mono',monospace;font-size:11px;margin-bottom:10px;outline:none}
  .search:focus{border-color:var(--g)}
  .footer{text-align:center;font-size:9px;color:var(--d);margin-top:10px}
</style>
</head>
<body>
<div class="header">
  <div><h1>TRADING BOT PRO</h1><div style="font-size:9px;color:var(--d)" id="upd">—</div></div>
  <div class="pill"><div class="dot"></div>PAPER · 80 ACTIVOS</div>
</div>
<div class="metrics">
  <div class="m"><div class="ml">Capital</div><div class="mv g" id="cap">$—</div></div>
  <div class="m"><div class="ml">P&L</div><div class="mv" id="pnl" style="color:var(--g)">$—</div></div>
  <div class="m"><div class="ml">Señales</div><div class="mv y" id="nsig">0</div></div>
  <div class="m"><div class="ml">Trades</div><div class="mv b" id="ntrades">0</div></div>
</div>
<div class="tabs">
  <div class="tab active" onclick="showTab('señales')">🟢 SEÑALES</div>
  <div class="tab" onclick="showTab('mercado')">📊 MERCADO</div>
  <div class="tab" onclick="showTab('ops')">📋 OPERACIONES</div>
  <div class="tab" onclick="showTab('logp')">🔍 LOG</div>
</div>
<div id="señales" class="panel active">
  <div id="sig-grid" class="signals-grid"></div>
  <div id="no-sig" style="text-align:center;padding:30px;color:var(--d);font-size:11px">⏳ Esperando señales de compra...</div>
</div>
<div id="mercado" class="panel">
  <input class="search" id="search" placeholder="Buscar activo..." oninput="filtrar()">
  <table class="table"><thead><tr><th>Activo</th><th>Precio</th><th>EMA9</th><th>RSI</th><th>Señal</th></tr></thead>
  <tbody id="tbody"></tbody></table>
</div>
<div id="ops" class="panel">
  <table class="table"><thead><tr><th>Hora</th><th>Tipo</th><th>Activo</th><th>Precio</th><th>Qty</th></tr></thead>
  <tbody id="ops-body"></tbody></table>
  <div id="no-ops" style="text-align:center;padding:30px;color:var(--d);font-size:11px">Sin operaciones aún</div>
</div>
<div id="logp" class="panel">
  <div class="log-box"><div id="log"></div></div>
</div>
<div class="footer">↻ Actualiza cada 10 segundos · Paper Trading</div>
<script>
let todosActivos={};
function showTab(id){
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',['señales','mercado','ops','logp'][i]===id));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}
function filtrar(){
  const q=document.getElementById('search').value.toLowerCase();
  renderTabla(Object.values(todosActivos).filter(a=>a.ticker.toLowerCase().includes(q)));
}
function renderTabla(activos){
  document.getElementById('tbody').innerHTML=activos.map(a=>`
    <tr>
      <td style="font-weight:700;color:var(--t)">${a.ticker}</td>
      <td style="color:var(--bl)">$${Number(a.precio).toLocaleString()}</td>
      <td style="color:var(--d)">${a.ema9}</td>
      <td style="color:${a.rsi>70?'var(--r)':a.rsi<30?'var(--g)':'var(--y)'}">${a.rsi}</td>
      <td><span class="badge ${a.señal==='COMPRAR'?'buy':a.señal==='VENDER'?'sell':'wait'}">${a.señal}</span></td>
    </tr>`).join('');
}
async function update(){
  try{
    const d=await(await fetch('/api/estado')).json();
    document.getElementById('cap').textContent='$'+d.capital.toFixed(2);
    const pnl=d.pnl,pe=document.getElementById('pnl');
    pe.textContent=(pnl>=0?'+':'')+'$'+Math.abs(pnl).toFixed(2);
    pe.style.color=pnl>=0?'var(--g)':'var(--r)';
    document.getElementById('nsig').textContent=d.señales.length;
    document.getElementById('ntrades').textContent=d.trades;
    document.getElementById('upd').textContent='Update: '+new Date().toLocaleTimeString('es-ES');
    const grid=document.getElementById('sig-grid'),noSig=document.getElementById('no-sig');
    if(d.señales.length>0){
      noSig.style.display='none';
      grid.innerHTML=d.señales.map(s=>`
        <div class="sig-card">
          <div class="sig-ticker">▲ ${s.ticker}</div>
          <div class="sig-price">$${Number(s.precio).toLocaleString()}</div>
          <div class="sig-info">RSI: ${s.rsi} · EMA9: ${s.ema9}</div>
          <div class="sig-info" style="color:var(--g)">EMA cruzó al alza ✓</div>
        </div>`).join('');
    }else{noSig.style.display='block';grid.innerHTML='';}
    todosActivos=d.activos;
    filtrar();
    const opsBody=document.getElementById('ops-body'),noOps=document.getElementById('no-ops');
    if(d.operaciones&&d.operaciones.length>0){
      noOps.style.display='none';
      opsBody.innerHTML=d.operaciones.map(o=>`
        <tr>
          <td style="color:var(--d)">${o.time}</td>
          <td><span class="badge ${o.tipo==='COMPRA'?'buy':'sell'}">${o.tipo}</span></td>
          <td style="font-weight:700">${o.ticker}</td>
          <td style="color:var(--bl)">$${Number(o.precio).toLocaleString()}</td>
          <td style="color:var(--d)">${o.qty}</td>
        </tr>`).join('');
    }else{noOps.style.display='block';}
    document.getElementById('log').innerHTML=d.log.map(e=>`
      <div class="le"><span class="lt">${e.time}</span><span class="l${e.tipo}">${e.msg}</span></div>`).join('');
  }catch(e){console.error(e);}
}
update();setInterval(update,10000);
</script>
</body>
</html>
"""

app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(DASHBOARD)

@app.route("/api/estado")
def api_estado():
    return jsonify(estado)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
