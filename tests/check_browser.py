"""Render the dashboard in installed Chrome or Edge. Run on the Windows host.

    py -3.12 tests/check_browser.py
"""
import html
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cockpit import State, handler_for
from demo import sample

# In this test server only, insert a same-origin QA script after the production
# app. It inspects the real DOM/layout and clicks the same buttons as the user.
SCRIPT = """
document.addEventListener('DOMContentLoaded', () => {
  const output = document.createElement('output');
  output.id = 'qa-result';
  document.body.append(output);
  let tries = 0;
  async function inspect() {
    if (!document.querySelector('#motion .metric') && tries++ < 80) {
      setTimeout(inspect, 50);
      return;
    }
    try {
      function check(ok, detail) { if (!ok) throw new Error(detail); }
      check(document.querySelector('#mode').textContent.includes('DEMO'), 'did not receive demo state');
      const buttons = [...document.querySelectorAll('[data-page]')];
      check(buttons.map(tab=>tab.dataset.page).join(',') === 'overview,player,flight,systems,log', 'tab order');
      check(buttons[0].getAttribute('aria-current') === 'page' && !document.querySelector('#overview').hidden, 'initial view');
      const card = id => document.getElementById(id).closest('.instrument-group').getBoundingClientRect();
      const player = card('player-title'), flight = card('flight-title');
      check(innerWidth > 760 ? player.right <= flight.left && player.top === flight.top : player.bottom <= flight.top,
            'responsive Player / Flight ordering');
      const labels = id => [...document.querySelectorAll(id + ' .metric-label')].map(node=>node.textContent).join(',');
      check(labels('#primaries') === 'Heart rate,Body temperature', 'player readings');
      check(labels('#motion') === 'Speed,Radar altitude,Vertical speed,Heading', 'flight readings');
      check(getComputedStyle(document.getElementById('motion')).gridTemplateColumns.split(' ').length === 2,'2x2 flight grid');

      const theme = () => document.documentElement.dataset.theme;
      const saved = sessionStorage.getItem('test-reload-theme');
      if (!saved) {
        check(theme() === (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'), 'initial system theme');
        const other = theme() === 'dark' ? 'light' : 'dark';
        document.getElementById('theme-' + other).click();
        sessionStorage.setItem('test-reload-theme',other);
        location.reload();
        return;
      }
      check(theme() === saved, 'explicit theme restored on reload');
      for (const name of ['light','dark']) {
        document.getElementById('theme-' + name).click();
        check(document.getElementById('theme-' + name).getAttribute('aria-pressed') === 'true','selected theme');
        for (const button of buttons) {
          button.click();
          check(!document.getElementById(button.dataset.page).hidden, button.textContent);
          check(document.documentElement.scrollWidth <= innerWidth, 'overflow on ' + button.dataset.page);
          check(getComputedStyle(document.body).backgroundColor ===
            (name === 'dark' ? 'rgb(16, 26, 39)' : 'rgb(242, 245, 248)'), 'theme on ' + button.dataset.page);
        }
        verifyLight('Gear deployed','true','Deployed',name === 'dark' ? 'rgb(121, 228, 165)' : 'rgb(33, 135, 88)');
        verifyLight('Landed','false','Airborne',name === 'dark' ? 'rgb(255, 142, 134)' : 'rgb(189, 72, 60)');
        const config = document.getElementById('sys-config');
        check(!config.textContent.includes('Transformation deployed'), 'missing transformation hidden');
        const valid = latest;
        const sections = valid.dashboard.sections;
        for (const [value, state] of [[null,'unavailable'], ['Off','false'], ['On','true']]) {
          const vehicle = {...sections.vehicle, power:{Engines:value}};
          displaySample({...valid, dashboard:{...valid.dashboard, sections:{...sections, vehicle}}});
          const led = document.querySelector('#power [data-reading="Engines powered"] .indicator-light');
          check(led && led.dataset.state === state, 'engine power after telemetry change');
          check(!document.querySelector('#power [data-reading="Gravity powered"]'), 'never reported power hidden');
        }
        const indicators = document.getElementById('indicators');
        for (const exact of ['Nominal', 'Off', 'Minor', 'Minor · flashing', 'Major', 'Major · flashing', null]) {
          const vehicle = {...sections.vehicle, annunciators:{'Power warning':exact}};
          displaySample({...valid, dashboard:{...valid.dashboard, sections:{...sections, vehicle}}});
          const led = indicators.querySelector('[data-reading="Power warning"] .indicator-light');
          if (exact === null) {
            check(led && led.dataset.state === 'unavailable', 'previously reported warning stays visible');
          } else {
            const warning = exact.startsWith('Minor') || exact.startsWith('Major');
            const color = warning ? (name === 'dark' ? 'rgb(255, 142, 134)' : 'rgb(189, 72, 60)')
                                  : (name === 'dark' ? 'rgb(121, 228, 165)' : 'rgb(33, 135, 88)');
            check(led && getComputedStyle(led).backgroundColor === color, exact + ' warning color');
            check(led.title.includes(exact) && led.title === led.getAttribute('aria-label'), exact + ' description');
          }
        }
        const vehicle = {...sections.vehicle, configuration:{
          ...sections.vehicle.configuration, transformation:'Deployed', doors:'Open'}};
        displaySample({...latest, dashboard:{...latest.dashboard, sections:{...sections, vehicle}}});
        check(config.textContent.includes('Transformation deployed'), 'reported transformation restored');
        check(config.querySelector('[data-reading="Doors closed"] .indicator-light').dataset.state === 'false',
              'open doors remain visible');
        const actor = {...valid.dashboard.sections.actor, health:0, oxygen:null, context:'EVA'};
        displaySample({...valid, dashboard:{...valid.dashboard, sections:{actor}}, ages:{actor:7}});
        check(document.getElementById('health-value').textContent === '0%', 'empty health gauge');
        check(!document.getElementById('oxygen-gauge').hidden && document.getElementById('oxygen-value').textContent === '—' && !document.getElementById('health-gauge').hidden,
              'missing oxygen and zero health visible');
        check(!config.children.length && !document.getElementById('reserves').children.length, 'absent ship fields hidden');
        check(document.getElementById('health-gauge').getAttribute('aria-valuetext').includes('Last known'), 'stale gauge');
        check(document.getElementById('overview-ship').textContent === 'No active ship', 'EVA player');
        displaySample(valid);
        check(!document.getElementById('oxygen-gauge').hidden && document.getElementById('oxygen-arc').getAttribute('visibility') === 'visible','fresh gauge update');
      }
      document.querySelector('[data-page="flight"]').click();
      document.querySelector('[data-window="60"]').click();
      check(document.querySelector('[data-window="60"]').getAttribute('aria-pressed')==='true','history range');
      check(document.querySelector('#recent-events time') && !document.querySelector('#flight img'), 'recent events');
      document.querySelector('#open-log').click();
      check(!document.querySelector('#log').hidden, 'open Event Log');
      document.querySelector('[data-page="systems"]').click();
      function verifyLight(label, state, exact, background) {
        const led = [...document.querySelectorAll('#sys-config .indicator-light')].find(node=>node.title.startsWith(label + ':'));
        check(led && led.dataset.state === state && led.title.includes(exact), label + ' state');
        check(led.title === led.getAttribute('aria-label') && led.getAttribute('role') === 'img', label + ' accessibility');
        check(getComputedStyle(led).backgroundColor === background, label + ' color');
      }
      {
        const stamp = '2026-11-21T05:04:00Z';
        const info = latest.dashboard.kill_monitor;
        info.last_telemetry_report = {time:stamp, value:'Died'};
        renderLog();
        const read = id => document.getElementById(id).textContent;
        check(!document.getElementById('overview-kill-panel').hidden, 'overview casualty report visible');
        check(read('overview-kill-report').includes('Died') && !read('kill-monitor').includes('Attacker'), 'telemetry-only death');
        info.last_death = {time:stamp, value:'Weapon: laser · Damage: Energy'};
        renderLog();
        check(read('overview-kill-report').includes('Weapon: laser · Damage: Energy'), 'overview death details');
        check(read('kill-monitor').includes('Weapon: laser · Damage: Energy'), 'event log death details');
      }
      {
        const valid = latest;
        const health = document.querySelector('#primaries strong').textContent;
        displaySample({...valid, dashboard:{...valid.dashboard, sections:null}});
        check(document.getElementById('connection').textContent === 'Dashboard display error', 'view failure reported');
        check(document.querySelector('#primaries strong').textContent === health, 'last instruments preserved');
        check(JSON.parse(document.getElementById('raw').textContent).actor != null, 'raw frame preserved');
        displaySample(valid);
        check(document.getElementById('connection').textContent === 'Receiving telemetry', 'next sample recovers');
      }
      document.getElementById('source').click();
      check(document.getElementById('feed-dialog').open && document.getElementById('diag-fields').rows.length,
            'feed indicator opens diagnostics');
      await document.getElementById('record-start').onclick();
      check(!document.getElementById('recording-lamp').hidden, 'recording indicator');
      document.getElementById('mark-label').value = 'Before disconnect';
      await document.getElementById('record-mark').onclick();
      await document.getElementById('record-stop').onclick();
      check(document.getElementById('recording-lamp').hidden, 'recording stopped');
      await document.getElementById('record-open').onclick();
      check(latest.mode === 'replay' && !document.getElementById('replay-tools').hidden, 'open saved replay');
      check(document.getElementById('replay-marks').options[1].textContent.includes('Before disconnect'), 'bookmark available');
      await document.getElementById('replay-play').onclick();
      const seek = document.getElementById('replay-seek');
      seek.value = seek.max;
      await seek.onchange({target:seek});
      check(latest.dashboard.feed === 'disconnected', 'seek to interruption');
      seek.value = 0;
      await seek.onchange({target:seek});
      check(latest.dashboard.feed === 'live' && latest.dashboard.sections.actor, 'seek restores fresh sample');
      await document.getElementById('replay-return').onclick();
      check(latest.mode === 'demo' && latest.dashboard.feed === 'disconnected', 'return to independent source');
      output.textContent='PASS';
    } catch (error) { output.textContent=error.message; }
  }
  window.addEventListener('load',inspect,{once:true});
});
"""


def browser_path():
    for base in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        for relative in ('Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe'):
            path = Path(os.environ.get(base, '')) / relative
            if path.is_file():
                return str(path)
    return shutil.which('chromium') or shutil.which('google-chrome')


def main():
    browser = browser_path()
    if not browser:
        sys.exit('Chrome or Edge is required for the rendered layout check.')
    recordings = tempfile.TemporaryDirectory()
    state = State(demo=True, recording_directory=recordings.name)

    class BrowserCheck(handler_for(state)):
        def do_GET(self):
            if self.path == '/qa.js':
                self.send(200, SCRIPT.encode(), 'text/javascript')
            else:
                if self.path == '/api/state' and not state.offline:
                    state.ingest(sample('Approach', 0))
                super().do_GET()

        def do_POST(self):
            if self.path == '/api/recording' and state.recordings.db:
                state.clear('Test interruption')
            super().do_POST()

        def send(self, code, body, content_type='application/json'):
            if self.path == '/' and isinstance(body, bytes):
                body = body.replace(b'</body>', b'<script src="/qa.js"></script></body>')
            super().send(code, body, content_type)

    with ThreadingHTTPServer(('127.0.0.1', 0), BrowserCheck) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for width, height in [(1440, 940), (390, 844)]:
                state.ingest(sample('Approach', 0))
                with tempfile.TemporaryDirectory() as profile:
                    result = subprocess.run([browser, '--headless', '--disable-gpu', '--no-first-run',
                            '--disable-background-networking', '--hide-scrollbars', '--user-data-dir=' + profile,
                            f'--window-size={width},{height}', '--virtual-time-budget=7000', '--dump-dom',
                            f'http://127.0.0.1:{server.server_port}/'], capture_output=True, timeout=45)
                match = re.search(r'<output id="qa-result">([^<]*)</output>', result.stdout.decode(errors='replace'))
                verdict = html.unescape(match[1]) if match else 'browser did not return the test result'
                if result.returncode or verdict != 'PASS':
                    sys.exit(f'{width}x{height}: {verdict}')
                print(f'{width}x{height}: passed')
        finally:
            server.shutdown()
            recordings.cleanup()


if __name__ == '__main__':
    main()
