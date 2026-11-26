#!/usr/bin/env bash
# Render Web Service-এর জন্য একটি পোর্ট খোলা থাকতে হয়।
# তাই ছোট একটা হেলথ-সার্ভার ব্যাকগ্রাউন্ডে চালিয়ে মূল বট (bot.py) চালানো হচ্ছে।
# bot.py-তে কোনো পরিবর্তন করা হয়নি।

PORT="${PORT:-10000}"

# Render-এ ভেরিয়েবলের নাম শুধু TOKEN; বট BOT_TOKEN খোঁজে, তাই এখানে মিলিয়ে দেওয়া হলো
export BOT_TOKEN="${BOT_TOKEN:-$TOKEN}"

python - <<PY &
import http.server, socketserver
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    do_HEAD = do_GET
    def log_message(self, *a): pass
socketserver.TCPServer.allow_reuse_address = True
socketserver.ThreadingTCPServer(("0.0.0.0", $PORT), H).serve_forever()
PY

# Render URL অটো-ডিটেক্ট + প্রতি ১৪ মিনিটে পিং (keepalive.py)
python keepalive.py &

exec python bot.py
