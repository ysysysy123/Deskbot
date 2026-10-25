import http.server
import ssl
import socket
import os
import sys
import threading
import datetime

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
WLAN_IP = "192.168.10.115"

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

class HttpRedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        target = f"https://{WLAN_IP}{self.path}"
        html = f"""<!DOCTYPE html>
<html>
<head>
<meta http-equiv="refresh" content="0;url={target}">
<title>Redirecting to Secure Console...</title>
</head>
<body style="font-family:sans-serif;text-align:center;padding:40px;background:#0b0f19;color:#e2e8f0;">
  <h2>正在跳转到 Deskbot 蓝牙安全控制台...</h2>
  <p>Web Bluetooth 需要安全通道 (HTTPS)</p>
  <p><a href="{target}" style="color:#00e5ff;font-size:18px;">点击此处直接前往: {target}</a></p>
  <script>window.location.href = "{target}";</script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def log_message(self, format, *args):
        pass

def generate_cert():
    cert_file = os.path.join(DIRECTORY, 'server.pem')
    if os.path.exists(cert_file):
        return cert_file
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        import ipaddress

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, u"Deskbot Console"),
        ])
        
        san_list = [
            x509.DNSName(u"localhost"),
            x509.IPAddress(ipaddress.IPv4Address(WLAN_IP)),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]

        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.datetime.now(datetime.timezone.utc)
        ).not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
        ).add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False,
        ).sign(key, hashes.SHA256())

        with open(cert_file, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ))
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        return cert_file
    except Exception as e:
        print(f"SSL certificate generation error: {e}", flush=True)
        return None

def start_server(host, port, ssl_context=None, handler_class=CustomHandler):
    try:
        server = http.server.ThreadingHTTPServer((host, port), handler_class)
        if ssl_context:
            server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
        proto = "HTTPS" if ssl_context else "HTTP"
        print(f"[{proto}] Listening on {host}:{port}", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"Error starting port {port}: {e}", flush=True)

if __name__ == '__main__':
    cert_path = generate_cert()
    ssl_ctx = None
    if cert_path and os.path.exists(cert_path):
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(certfile=cert_path)

    print("=" * 55, flush=True)
    print("   Deskbot BLE Multi-Port Console Server", flush=True)
    print("=" * 55, flush=True)
    print(f"Mobile Direct:   https://{WLAN_IP}", flush=True)
    print(f"Mobile Alt Port: https://{WLAN_IP}:8443", flush=True)
    print(f"Mobile HTTP:     http://{WLAN_IP}", flush=True)
    print(f"PC Browser:      http://localhost:8000", flush=True)
    print(f"PC HTTPS:        https://localhost:8443", flush=True)
    print("=" * 55, flush=True)

    threads = []
    # 1. Port 443 (HTTPS)
    if ssl_ctx:
        t1 = threading.Thread(target=start_server, args=('0.0.0.0', 443, ssl_ctx), daemon=True)
        threads.append(t1)
        t1.start()

    # 2. Port 8443 (HTTPS)
    if ssl_ctx:
        t2 = threading.Thread(target=start_server, args=('0.0.0.0', 8443, ssl_ctx), daemon=True)
        threads.append(t2)
        t2.start()

    # 3. Port 80 (HTTP redirect to HTTPS)
    t3 = threading.Thread(target=start_server, args=('0.0.0.0', 80, None, HttpRedirectHandler), daemon=True)
    threads.append(t3)
    t3.start()

    # 4. Port 8000 (HTTP direct for localhost)
    t4 = threading.Thread(target=start_server, args=('0.0.0.0', 8000, None, CustomHandler), daemon=True)
    threads.append(t4)
    t4.start()

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nServer stopped.")
