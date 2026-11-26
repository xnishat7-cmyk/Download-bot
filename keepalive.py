"""Render URL অটো-ডিটেক্ট করে প্রতি ১৪ মিনিটে পিং করে এবং লগে স্ট্যাটাস দেখায়।"""
import os, time, urllib.request

INTERVAL = 14 * 60


def detect_url():
    # 1) Render নিজে যে URL সেট করে দেয়
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if url:
        return url.rstrip("/")
    # 2) হোস্টনেম থেকে বানানো
    host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if host:
        return "https://" + host
    # 3) সার্ভিসের নাম থেকে আন্দাজ
    name = os.environ.get("RENDER_SERVICE_NAME")
    if name:
        return f"https://{name}.onrender.com"
    # 4) নিজে দিতে চাইলে (ঐচ্ছিক) PING_URL ভেরিয়েবল
    return os.environ.get("PING_URL")


url = detect_url()
if not url:
    print("[keepalive] Render URL পাওয়া যায়নি, পিং বন্ধ।", flush=True)
    raise SystemExit

print(f"[keepalive] URL detected: {url} | every {INTERVAL // 60} min", flush=True)

while True:
    time.sleep(INTERVAL)
    try:
        code = urllib.request.urlopen(url, timeout=30).status
        print(f"[keepalive] ping OK ({code}) {time.strftime('%H:%M:%S')}", flush=True)
    except Exception as e:
        print(f"[keepalive] ping failed: {e}", flush=True)
