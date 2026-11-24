'use strict';
const element = id => document.getElementById(id);
const address = element('share-address');
const start = element('start-share');
const stop = element('stop-share');
const url = element('share-url');
const statusLine = element('share-status');
try {
  const theme = localStorage.getItem('sc-cockpit.theme');
  if (['light','dark'].includes(theme)) document.documentElement.dataset.theme = theme;
} catch {}
function show(state) {
  const previous = address.value;
  address.replaceChildren(...state.addresses.map(ip => new Option(ip,ip)));
  if (state.url) address.value = new URL(state.url).hostname;
  else if (state.addresses.includes(previous)) address.value = previous;
  address.disabled = Boolean(state.url);
  start.hidden = Boolean(state.url);
  start.disabled = !address.value;
  stop.disabled = false;
  element('share-link-panel').hidden = !state.url;
  url.value = state.url || '';
  const qr = element('share-qr');
  if (state.url) qr.src = '/api/share/qr?' + Date.now();
  else qr.removeAttribute('src');
  statusLine.textContent = state.url ? 'Sharing is on. Stopping sharing expires this link.'
    : address.value ? 'Sharing is off.' : 'No private IPv4 address found. Connect the PC to your home network and reload.';
}
async function update(command) {
  start.disabled = stop.disabled = true;
  try {
    const response = await fetch('/api/share',command && {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(command)});
    const state = await response.json();
    if (!response.ok) throw Error(state.error);
    show(state);
  } catch (error) {
    statusLine.textContent = error.message || 'Unable to reach the reader.';
    start.disabled = !address.value;
    stop.disabled = false;
  }
}
start.onclick = () => update({action:'start',address:address.value});
stop.onclick = () => update({action:'stop'});
element('copy-share').onclick = async () => {
  try {
    await navigator.clipboard.writeText(url.value);
    statusLine.textContent = 'Display link copied.';
  } catch {
    url.select();
    statusLine.textContent = 'Press Ctrl+C to copy the selected link.';
  }
};
window.addEventListener('pageshow', () => update());
