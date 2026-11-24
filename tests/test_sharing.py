import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cockpit import State, handler_for
from demo import sample
from sharing import DisplayHandler, DisplaySharing


def request(handler, server, path, *, host, peer='192.168.1.20', post=None, origin=None):
    body = json.dumps(post).encode() if post is not None else b''
    headers = [f'{"POST" if post is not None else "GET"} {path} HTTP/1.0',
               f'Host: {host}', f'Content-Length: {len(body)}']
    if origin:
        headers.append('Origin: ' + origin)
    wire = ('\r\n'.join(headers) + '\r\n\r\n').encode() + body
    out = io.BytesIO()
    sock = SimpleNamespace(makefile=lambda *args, **kwargs: io.BytesIO(wire), sendall=out.write)
    handler(sock, (peer, 1234), server)
    head, body = out.getvalue().split(b'\r\n\r\n', 1)
    return int(head.split()[1]), body


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.state = State(demo=True)
        self.host = '192.168.1.7:8745'
        self.server = SimpleNamespace(state=self.state, authority=self.host, token='display-session')

    def view(self, route, **kwargs):
        return request(DisplayHandler, self.server, '/display-session/' + route,
                       host=kwargs.pop('host', self.host), **kwargs)

    def test_only_display_routes_including_when_host_is_spoofed(self):
        code, html = self.view('')
        self.assertEqual(code, 200)
        self.assertIn(b'data-tablet', html)
        for route in ('app.js', 'style.css', 'api/state'):
            self.assertEqual(self.view(route)[0], 200)
        for route in ('api/share', 'api/recordings', 'api/replay', 'share.js', 'api/share/qr'):
            self.assertEqual(self.view(route)[0], 404)
        for route in ('api/share', 'api/rules', 'api/scenario', 'api/recording'):
            self.assertEqual(self.view(route, post={'action': 'stop'})[0], 405)
        self.assertEqual(self.view('api/state', host='127.0.0.1:8743')[0], 403)
        self.assertEqual(self.view('api/state', peer='203.0.113.1')[0], 403)
        self.assertEqual(request(DisplayHandler, self.server, '/api/state', host=self.host)[0], 404)

    def test_shared_panel_freshness_visibility_and_omissions(self):
        self.assertEqual(self.state.snapshot(display_only=True)['dashboard']['feed'], 'waiting')
        frame = sample('Approach', 0)
        frame['actor']['state']['uec_balance'] = '123456'
        frame['vehicle']['state']['flight']['instruments']['speed'] = 0
        frame['vehicle']['state']['power']['engines'] = False
        self.state.ingest(frame, 20)
        with patch('time.monotonic', return_value=20):
            data = json.loads(self.view('api/state')[1])
        panels = data['dashboard']['sections']
        self.assertEqual(panels['vehicle']['flight']['speed'], 0)
        self.assertEqual(panels['vehicle']['power']['Engines'], 'Off')
        self.assertTrue({'frame','diagnostics','rules','recording'}.isdisjoint(data))
        self.assertTrue({'performance','activity','kill_monitor'}.isdisjoint(data['dashboard']))
        self.assertNotIn('uec_balance', panels['actor'])
        self.assertNotIn('uec_balance', data['dashboard']['reported']['actor'])
        self.assertEqual(self.state.snapshot(20)['dashboard']['sections']['actor']['uec_balance'], '123456')
        self.assertEqual(self.state.snapshot(27, display_only=True)['dashboard']['feed'], 'stale')
        self.state.ingest(sample('EVA', 0), 28)
        self.assertNotIn('vehicle', self.state.snapshot(28, display_only=True)['dashboard']['sections'])
        self.state.clear('Game closed', now=29)
        self.assertEqual(self.state.snapshot(29, display_only=True)['dashboard']['feed'], 'disconnected')

    @patch('sharing.local_addresses', return_value=['192.168.1.7'])
    @patch('sharing.ThreadingHTTPServer')
    def test_local_start_stop_and_link_rotation(self, listener, addresses):
        listener.side_effect = lambda addr, handler: MagicMock(server_address=addr, server_port=addr[1])
        share = DisplaySharing(self.state)
        local = handler_for(self.state, share)
        host = 'localhost:8743'
        def manage(route='api/share', **kwargs):
            return request(local, SimpleNamespace(server_port=8743), '/' + route, host=host, **kwargs)
        self.assertIsNone(json.loads(manage()[1])['url'])
        listener.assert_not_called()
        start = {'action':'start','address':'192.168.1.7'}
        self.assertEqual(manage(post=start, origin='http://untrusted.example')[0], 403)
        origin = 'http://' + host
        self.assertEqual(manage(post=dict(start,address='0.0.0.0'), origin=origin)[0], 400)
        link = json.loads(manage(post=start, origin=origin)[1])['url']
        self.assertTrue(link.startswith('http://' + self.host + '/'))
        self.assertIn(b'<svg', manage('api/share/qr')[1])
        self.assertEqual(json.loads(manage(post=start, origin=origin)[1])['url'],link)
        self.assertEqual(listener.call_count, 1)
        running = share.server
        self.assertIsNone(json.loads(manage(post={'action':'stop'}, origin=origin)[1])['url'])
        running.shutdown.assert_called_once()
        running.server_close.assert_called_once()
        self.assertEqual(manage('api/share/qr')[0], 404)
        self.assertNotEqual(json.loads(manage(post=start, origin=origin)[1])['url'],link)
        share.stop()
