"""Optional read-only display listener and the PC's local sharing controls."""
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

WEB = Path(__file__).resolve().parent / 'web'
DISPLAY_PORT = 8745
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(net) for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}


def private_ipv4(text):
    try:
        address = ipaddress.IPv4Address(text)
    except ipaddress.AddressValueError:
        return False
    return any(address in network for network in PRIVATE_NETWORKS)


def local_addresses():
    try:
        records = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({record[4][0] for record in records if private_ipv4(record[4][0])})


class DashboardHandler(BaseHTTPRequestHandler):
    def send(self, code, body, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def asset(self, entry, tablet=False):
        name, mime = entry
        body = (WEB / name).read_bytes()
        if tablet and name == 'index.html':
            body = body.replace(b'<html lang="en">', b'<html lang="en" data-tablet>')
        self.send(200, body, mime)

    def log_message(self, *args):
        pass


class DisplayHandler(DashboardHandler):
    def do_GET(self):
        server = self.server
        if self.headers.get('Host') != server.authority or not private_ipv4(self.client_address[0]):
            return self.send(403, {'error': 'Private network only'})
        path = urlsplit(self.path).path
        prefix = '/' + server.token
        if not path.startswith(prefix + '/'):
            return self.send(404, {'error': 'Display link unavailable'})
        route = path[len(prefix):]
        if route == '/api/state':
            return self.send(200, server.state.snapshot(display_only=True))
        if route in ASSETS:
            return self.asset(ASSETS[route], tablet=True)
        return self.send(404, {'error': 'Unknown display route'})

    def do_POST(self):
        self.send(405, {'error': 'This display is read-only'})


class DisplaySharing:
    def __init__(self, state):
        self.state = state
        self.lock = threading.RLock()
        self.server = None

    def status(self):
        with self.lock:
            url = f'http://{self.server.authority}/{self.server.token}/' if self.server else None
            return {'addresses': local_addresses(), 'url': url}

    def start(self, address):
        with self.lock:
            if address not in local_addresses():
                raise ValueError('Choose a private IPv4 address assigned to this PC.')
            if self.server:
                if self.server.server_address[0] != address:
                    raise ValueError('Stop sharing before selecting a different address.')
                return
            server = ThreadingHTTPServer((address, DISPLAY_PORT), DisplayHandler)
            server.state = self.state
            server.authority = f'{address}:{server.server_port}'
            server.token = secrets.token_urlsafe(16)
            try:
                threading.Thread(target=server.serve_forever, daemon=True).start()
            except RuntimeError:
                server.server_close()
                raise
            self.server = server

    def stop(self):
        with self.lock:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
                self.server = None


def qr_svg(url):
    from vendor.qrcodegen import QrCode
    code = QrCode.encode_text(url, QrCode.Ecc.MEDIUM)
    size = code.get_size()
    squares = ''.join(f'M{x+4} {y+4}h1v1h-1z' for y in range(size) for x in range(size) if code.get_module(x, y))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size+8} {size+8}" shape-rendering="crispEdges">'
            f'<rect width="100%" height="100%" fill="white"/><path d="{squares}"/></svg>').encode()
