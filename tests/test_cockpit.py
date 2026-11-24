import unittest
import io
import json
from types import SimpleNamespace
from cockpit import Alerts, State, DEFAULT_RULES, percent, validate_rules, handler_for
from extract_schema import varint, descriptor_end


class TelemetryTests(unittest.TestCase):
    def test_zero_is_real_and_missing_max_is_unknown(self):
        self.assertEqual(percent({'current': 0, 'max': 100}), 0)
        for value in ({'current': 0}, {'current': 1, 'max': 0}, {'current': float('nan'), 'max': 100}, {'current': True, 'max': 100}):
            self.assertIsNone(percent(value))

    def test_sections_expire_independently(self):
        state=State()
        state.ingest({'vehicle': {'state': {'name': 'Old ship'}}}, now=0)
        state.ingest({'actor': {'vitals': {'oxygen': {'current': 12, 'max': 100}}}}, now=4)
        data=state.snapshot(now=6)
        self.assertNotIn('vehicle', data['frame'])
        self.assertEqual(data['values']['oxygen'], 12)
        self.assertIn('oxygen', data['active'])
        data=state.snapshot(now=10)
        self.assertFalse(data['connected'])
        self.assertEqual(data['active'], [])
        self.assertEqual(data['events'], [])

    def test_on_foot_drops_vehicle(self):
        state=State()
        state.ingest({'vehicle': {'state': {'name': 'Ship'}}, 'actor': {'state': {'locomotion': 1}}},now=0)
        self.assertNotIn('vehicle',state.snapshot(now=1)['frame'])

    def test_reconnect_clears_previous_character(self):
        state=State()
        state.ingest({'actor': {'vitals': {'health': {'current': 10, 'max': 100}}}}, now=0)
        self.assertEqual(len(state.snapshot(now=0)['events']),1)
        state.clear('Disconnected')
        self.assertFalse(state.snapshot(now=1)['connected'])
        self.assertEqual(state.snapshot(now=1)['events'],[])

    def test_alert_ids_survive_reader_reset_without_collision(self):
        first, second = Alerts(), Alerts()
        for alerts in (first, second):
            alerts.update({'oxygen': 0}, {'oxygen': DEFAULT_RULES['oxygen']}, 0)
        self.assertNotEqual(first.events[0]['id'], second.events[0]['id'])

    def test_descriptor_walk_stops_at_padding_and_next_descriptor(self):
        data=b'\x0a\x07a.proto\x12\x03abc\x00padding'
        self.assertEqual(descriptor_end(data,0),14)
        self.assertEqual(descriptor_end(data[:14]+data,0),14)
        self.assertEqual(varint(b'\xac\x02',0),(300,2))

    def test_alert_hysteresis_cooldown_and_no_repeating(self):
        alerts=Alerts(); rules={'oxygen':DEFAULT_RULES['oxygen']}
        def update(value, now): return alerts.update({'oxygen':value},rules,now)
        update(19,0)
        self.assertEqual(alerts.counter,1)
        update(19,100)
        self.assertEqual(alerts.counter,1)
        update(22,101);update(19,102)
        self.assertEqual(alerts.counter,1)
        update(26,103);update(19,104)
        self.assertEqual(alerts.counter,2)
        update(26,105);update(19,106)
        self.assertEqual(alerts.counter,2)
        update(19,165)
        self.assertEqual(alerts.counter,3)

    def test_disabled_rules_and_invalid_settings(self):
        alerts=Alerts()
        self.assertEqual(alerts.update({'oxygen':0},{'oxygen':{'enabled':False,'threshold':20}},0),[])
        self.assertEqual(validate_rules(DEFAULT_RULES), DEFAULT_RULES)
        for value in ({}, {'health': {}}, dict(DEFAULT_RULES,oxygen={'enabled':True,'threshold':float('nan')})):
            with self.assertRaises(ValueError): validate_rules(value)


class TrayTests(unittest.TestCase):
    def test_status_uses_packets_and_recording_state(self):
        from cockpit import tray_status
        state = State()
        self.assertEqual(tray_status(state, 0), 'Waiting for telemetry')
        state.ingest({}, now=1)
        self.assertEqual(tray_status(state, 2), 'Receiving telemetry')
        self.assertEqual(tray_status(state, 6), 'Telemetry stale')
        state.clear('Stream ended', now=7)
        self.assertEqual(tray_status(state, 8), 'Waiting for telemetry')
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            state = State(demo=True, recording_directory=directory)
            state.ingest({}, now=1)
            state.recordings.start(state, 1)
            self.assertEqual(tray_status(state, 2), 'Demo telemetry · Recording')
            state.recordings.stop(state, 2)

    def test_private_pipe_closes_on_quit_or_parent_exit(self):
        from cockpit import watch_launcher
        from unittest.mock import Mock
        for message in ('quit\n', ''):
            server = Mock()
            watch_launcher(server, io.StringIO(message))
            server.shutdown.assert_called_once_with()


class HTTPTests(unittest.TestCase):
    def request(self, method, path, host='127.0.0.1:8743', origin=None, body=b''):
        class Socket:
            def __init__(self, data):
                self.input=io.BytesIO(data)
                self.output=io.BytesIO()
            def makefile(self, *args, **kwargs): return self.input
            def sendall(self, data): self.output.write(data)
        headers=f'{method} {path} HTTP/1.0\r\nHost: {host}\r\n'
        if origin: headers+=f'Origin: {origin}\r\n'
        headers+=f'Content-Length: {len(body)}\r\n\r\n'
        sock=Socket(headers.encode()+body)
        state=State(demo=True)
        handler_for(state)(sock, ('127.0.0.1',1), SimpleNamespace(server_port=8743))
        response=sock.output.getvalue()
        return response,state

    def test_assets_and_state(self):
        for path in ('/', '/app.js', '/style.css', '/api/state'):
            with self.subTest(path=path):
                response,_=self.request('GET',path)
                self.assertTrue(response.startswith(b'HTTP/1.0 200'))
                self.assertIn(b'Content-Security-Policy:',response)
        response,_=self.request('GET','/api/state')
        data=json.loads(response.split(b'\r\n\r\n',1)[1])
        self.assertEqual(data['mode'],'demo')
        self.assertFalse(data['connected'])

    def test_reject_foreign_host_and_cross_origin_mutation(self):
        response,_=self.request('GET','/api/state',host='untrusted.example')
        self.assertTrue(response.startswith(b'HTTP/1.0 403'))
        response,_=self.request('POST','/api/rules',origin='https://untrusted.example',body=json.dumps(DEFAULT_RULES).encode())
        self.assertTrue(response.startswith(b'HTTP/1.0 403'))

    def test_rules_update_and_malformed_body(self):
        rules=dict(DEFAULT_RULES,oxygen={'enabled':True,'threshold':32})
        response,state=self.request('POST','/api/rules',origin='http://127.0.0.1:8743',body=json.dumps(rules).encode())
        self.assertTrue(response.startswith(b'HTTP/1.0 200'))
        self.assertEqual(state.rules['oxygen']['threshold'],32)
        response,_=self.request('POST','/api/rules',origin='http://127.0.0.1:8743',body=b'{}')
        self.assertTrue(response.startswith(b'HTTP/1.0 400'))


if __name__ == '__main__':
    unittest.main()
