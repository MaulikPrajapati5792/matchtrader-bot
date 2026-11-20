import os
import time
import random
import logging
from datetime import datetime, date
from flask import Flask, request, jsonify, render_template_string
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)
SECRET_KEY = "ULTIMATE_SECRET_123"
PORT = int(os.environ.get("PORT", 5000))

# Prop Firm Rules & Lot Configurations
MAX_DAILY_TRADES = 5
TRADE_COOLDOWN_SEC = 180

MAX_VOLUME_TABLE = {
    "EURUSD": 5.00, "GBPUSD": 5.00, "USDJPY": 5.00, "AUDUSD": 5.00,
    "USDCAD": 5.00, "US30": 5.00, "NAS100": 5.00, "SPX500": 5.00,
    "GER40": 5.00, "XAUUSD": 3.00, "XTIUSD": 3.00, "BTCUSD": 1.00,
    "ETHUSD": 2.00, "DEFAULT": 1.00
}

SAFE_SL_TP = {
    "XAUUSD": {"sl": 50, "tp": 120},
    "US30":   {"sl": 50, "tp": 150},
    "NAS100": {"sl": 40, "tp": 120},
    "BTCUSD": {"sl": 100, "tp": 300},
    "DEFAULT":{"sl": 15, "tp": 40}
}

state = {
    "daily_trades": 0,
    "last_trade_date": date.today(),
    "last_trade_timestamp": 0,
    "ai_enabled": False,
    "logs": ["Engine initialized on Cloud Server."]
}

def log_event(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    logging.info(msg)
    state["logs"].append(formatted)
    if len(state["logs"]) > 40:
        state["logs"].pop(0)

def check_trade_limits():
    today = date.today()
    if state["last_trade_date"] != today:
        state["daily_trades"] = 0
        state["last_trade_date"] = today
        
    if state["daily_trades"] >= MAX_DAILY_TRADES:
        return False, f"Daily limit reached ({MAX_DAILY_TRADES}/{MAX_DAILY_TRADES})"
        
    elapsed = time.time() - state["last_trade_timestamp"]
    if elapsed < TRADE_COOLDOWN_SEC:
        rem = int(TRADE_COOLDOWN_SEC - elapsed)
        return False, f"Cooldown active ({rem}s left)"
        
    return True, "OK"

def execute_playwright_trade(symbol, action, sl_pips=None, tp_pips=None):
    valid, msg = check_trade_limits()
    if not valid:
        log_event(f"Blocked: {msg}")
        return False, msg

    symbol = symbol.upper()
    volume = MAX_VOLUME_TABLE.get(symbol, MAX_VOLUME_TABLE["DEFAULT"])
    defaults = SAFE_SL_TP.get(symbol, SAFE_SL_TP["DEFAULT"])
    sl_pips = sl_pips or defaults["sl"]
    tp_pips = tp_pips or defaults["tp"]

    profile_dir = os.path.join(os.path.expanduser("~"), "mtr_cloud_profile")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        try:
            log_event(f"Opening Match-Trader: {symbol} {action} ({volume} lots)")
            page.goto("https://mtr-competition.fundingpips.com/app/trade", wait_until="networkidle", timeout=35000)

            # 1. Asset Search
            search = page.locator("input[placeholder*='Search'], input[type='search']").first
            if search.is_visible(timeout=5000):
                search.fill(symbol)
                page.wait_for_timeout(1000)
                page.keyboard.press("Enter")

            # 2. Open Order Drawer
            action_btn = page.locator(f"button:has-text('{action}'), div:has-text('{action}')").first
            action_btn.click()
            page.wait_for_timeout(1500)

            # 3. Clean Volume Input (Clear concatenated defaults)
            vol_input = page.locator("input[formcontrolname='volume'], input[name='volume']").first
            if vol_input.is_visible():
                vol_input.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                vol_input.type(str(volume), delay=50)
                vol_input.evaluate("""el => {
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }""")

            # 4. Activate PIPS Mode for SL/TP
            pips_btns = page.locator("button:has-text('PIPS'), span:has-text('PIPS')").all()
            for b in pips_btns:
                try: b.click(timeout=800)
                except: pass

            # 5. Inject SL/TP Values
            sl_input = page.locator("input[formcontrolname='slPips'], input[name='slPips']").first
            if sl_input.is_visible():
                sl_input.click()
                sl_input.type(str(sl_pips))

            tp_input = page.locator("input[formcontrolname='tpPips'], input[name='tpPips']").first
            if tp_input.is_visible():
                tp_input.click()
                tp_input.type(str(tp_pips))

            # 6. Submit Trade
            submit_btn = page.locator("button[type='submit']:not([disabled])").first
            submit_btn.click()
            page.wait_for_timeout(2500)

            state["daily_trades"] += 1
            state["last_trade_timestamp"] = time.time()
            log_event(f"SUCCESS: {action} {symbol} placed successfully.")
            browser.close()
            return True, f"Successfully executed {action} {symbol}"

        except Exception as e:
            log_event(f"Execution Failed: {str(e)}")
            browser.close()
            return False, str(e)

# API Endpoints
@app.route("/api/status", methods=["GET"])
def get_status():
    elapsed = time.time() - state["last_trade_timestamp"]
    cooldown_rem = max(0, int(TRADE_COOLDOWN_SEC - elapsed))
    return jsonify({
        "daily_trades": state["daily_trades"],
        "max_trades": MAX_DAILY_TRADES,
        "cooldown_remaining": cooldown_rem,
        "ai_enabled": state["ai_enabled"],
        "logs": state["logs"]
    })

@app.route("/api/execute", methods=["POST"])
def api_execute():
    data = request.json or {}
    symbol = data.get("symbol", "XAUUSD")
    action = data.get("action", "BUY")
    success, msg = execute_playwright_trade(symbol, action)
    return jsonify({"success": success, "message": msg})

@app.route("/api/ai_toggle", methods=["POST"])
def toggle_ai():
    data = request.json or {}
    state["ai_enabled"] = data.get("enabled", False)
    log_event(f"AI Engine Mode set to: {'ACTIVE' if state['ai_enabled'] else 'PAUSED'}")
    return jsonify({"success": True, "ai_enabled": state["ai_enabled"]})

# iPhone Native Web App UI (HTML5/Apple Mobile Web App)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>MatchTrader AI</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif; background: #000; color: #fff; margin: 0; padding: 20px; -webkit-tap-highlight-color: transparent; }
        .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 15px; border-bottom: 1px solid #222; }
        .title { font-size: 20px; font-weight: 700; color: #fff; }
        .status-badge { font-size: 12px; padding: 4px 10px; border-radius: 12px; background: #1c1c1e; color: #34c759; font-weight: 600; }
        .card-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 15px 0; }
        .card { background: #1c1c1e; padding: 15px; border-radius: 14px; text-align: center; }
        .card-val { font-size: 22px; font-weight: 700; margin-top: 5px; }
        .card-lbl { font-size: 11px; color: #8e8e93; text-transform: uppercase; letter-spacing: 0.5px; }
        .ai-box { background: #1c1c1e; padding: 15px; border-radius: 14px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
        .switch { position: relative; width: 51px; height: 31px; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #3a3a3c; transition: .3s; border-radius: 34px; }
        .slider:before { position: absolute; content: ""; height: 27px; width: 27px; left: 2px; bottom: 2px; background-color: white; transition: .3s; border-radius: 50%; }
        input:checked + .slider { background-color: #34c759; }
        input:checked + .slider:before { transform: translateX(20px); }
        select { width: 100%; background: #1c1c1e; color: #fff; border: 1px solid #2c2c2e; padding: 14px; font-size: 16px; border-radius: 12px; margin-bottom: 15px; outline: none; }
        .btn-group { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }
        .btn { border: none; padding: 18px; font-size: 16px; font-weight: 700; border-radius: 14px; color: #fff; cursor: pointer; }
        .btn-buy { background: #34c759; }
        .btn-sell { background: #ff3b30; }
        .btn:disabled { opacity: 0.4; }
        .console { background: #000; border: 1px solid #1c1c1e; padding: 12px; border-radius: 12px; font-family: monospace; font-size: 11px; height: 160px; overflow-y: auto; color: #34c759; }
    </style>
</head>
<body>

    <div class="header">
        <div class="title">Match-Trader AI</div>
        <div class="status-badge" id="net-status">ONLINE</div>
    </div>

    <div class="card-grid">
        <div class="card">
            <div class="card-lbl">Daily Limit</div>
            <div class="card-val" id="trades-val">0 / 5</div>
        </div>
        <div class="card">
            <div class="card-lbl">Cooldown</div>
            <div class="card-val" id="cd-val" style="color:#ff9500;">0s</div>
        </div>
    </div>

    <div class="ai-box">
        <div>
            <div style="font-weight: 600;">85% BUY Bias AI</div>
            <div style="font-size: 11px; color: #8e8e93;">Auto-Executes High Probability Signals</div>
        </div>
        <label class="switch">
            <input type="checkbox" id="ai-toggle" onchange="toggleAI(this.checked)">
            <span class="slider"></span>
        </label>
    </div>

    <select id="symbol-select">
        <option value="XAUUSD">XAUUSD (Gold - Max 3.00 Lots)</option>
        <option value="EURUSD">EURUSD (Forex - Max 5.00 Lots)</option>
        <option value="GBPUSD">GBPUSD (Forex - Max 5.00 Lots)</option>
        <option value="US30">US30 (Index - Max 5.00 Lots)</option>
        <option value="NAS100">NAS100 (Index - Max 5.00 Lots)</option>
        <option value="BTCUSD">BTCUSD (Crypto - Max 1.00 Lot)</option>
    </select>

    <div class="btn-group">
        <button class="btn btn-buy" id="buy-btn" onclick="sendTrade('BUY')">BUY</button>
        <button class="btn btn-sell" id="sell-btn" onclick="sendTrade('SELL')">SELL</button>
    </div>

    <div style="font-size: 12px; color: #8e8e93; margin-bottom: 6px;">Live Cloud Engine Log</div>
    <div class="console" id="log-box"></div>

    <script>
        function updateUI() {
            fetch('/api/status')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('trades-val').innerText = data.daily_trades + ' / ' + data.max_trades;
                    document.getElementById('cd-val').innerText = data.cooldown_remaining + 's';
                    document.getElementById('ai-toggle').checked = data.ai_enabled;
                    
                    const isLocked = data.cooldown_remaining > 0 || data.daily_trades >= data.max_trades;
                    document.getElementById('buy-btn').disabled = isLocked;
                    document.getElementById('sell-btn').disabled = isLocked;

                    const logBox = document.getElementById('log-box');
                    logBox.innerHTML = data.logs.slice().reverse().join('<br>');
                });
        }

        function sendTrade(action) {
            const sym = document.getElementById('symbol-select').value;
            fetch('/api/execute', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ symbol: sym, action: action })
            });
            setTimeout(updateUI, 500);
        }

        function toggleAI(enabled) {
            fetch('/api/ai_toggle', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ enabled: enabled })
            });
        }

        setInterval(updateUI, 2000);
        updateUI();
    </script>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML_TEMPLATE)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)