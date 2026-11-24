'use strict';
const names = {health:'Health',oxygen:'Oxygen',hunger:'Nutrition',thirst:'Hydration'};
const $ = id => document.getElementById(id);
let audio = null, armed = false, muted = false, hydrated = false, seen = new Set(), latest = null;
let volume = .4, pendingVoice = null, lastSound = -Infinity;
for (const [key, name] of Object.entries(names)) {
  const row=document.createElement('div');row.className='rule';
  const check=document.createElement('input');check.type='checkbox';check.id=`enabled-${key}`;
  const title=document.createElement('label');title.append(check,document.createTextNode(name));
  const threshold=document.createElement('input');threshold.type='number';threshold.min=1;threshold.max=99;threshold.step=1;threshold.id=`threshold-${key}`;threshold.setAttribute('aria-label',`${name} warning threshold in percent`);
  const unit=document.createElement('span');unit.textContent='%'; row.append(title,threshold,unit);$('rule-rows').append(row);
}
function audioStatus(){ $('audio-status').replaceChildren(light('Alert playback enabled', muted?'Muted':armed?'Enabled':'Off', ['Enabled']));$('mute').textContent=muted?'Unmute':'Mute';$('mute').setAttribute('aria-pressed',String(muted)); }
async function enableAudio(){
  const Context=window.AudioContext||window.webkitAudioContext;
  if(!Context)throw new Error('Audio is unavailable in this browser.');
  audio??=new Context();await audio.resume();armed=true;audioStatus();
}
function tone(){
  if(!audio||audio.state!=='running')return;
  const osc=audio.createOscillator(),gain=audio.createGain(),now=audio.currentTime;
  osc.frequency.value=660;gain.gain.setValueAtTime(0,now);gain.gain.linearRampToValueAtTime(volume*.16,now+.03);gain.gain.linearRampToValueAtTime(0,now+.3);osc.connect(gain);gain.connect(audio.destination);osc.start(now);osc.stop(now+.31);
}
function sound(text){
  if(!armed||muted||document.hidden)return;
  tone();
  if('speechSynthesis' in window){
    window.speechSynthesis.cancel();
    const message=new SpeechSynthesisUtterance(text);message.volume=volume;message.rate=.95;window.speechSynthesis.speak(message);
  }
}
$('enable-audio').onclick=async()=>{try{await enableAudio();if(latest?.active.length&&!muted){pendingVoice={keys:[...latest.active],at:performance.now()};}else tone();}catch(e){$('save-status').textContent=e.message;}};
$('test-audio').onclick=async()=>{try{await enableAudio();if(muted){$('save-status').textContent='Unmute to test audio.';return;}sound('Cockpit audio check.');}catch(e){$('save-status').textContent=e.message;}};
$('mute').onclick=()=>{muted=!muted;pendingVoice=null;window.speechSynthesis?.cancel();audioStatus();};
$('volume').oninput=e=>{volume=Number(e.target.value)/100;$('volume-value').textContent=`${e.target.value}%`;};
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{$('save-status').textContent='Full screen unavailable.';}};
$('rules').onsubmit=async e=>{
  e.preventDefault();if (playback) return;const rules={};
  for(const key of Object.keys(names))rules[key]={enabled:$(`enabled-${key}`).checked,threshold:Number($(`threshold-${key}`).value)};
  try{const response=await fetch('/api/rules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(rules)});if(!response.ok)throw new Error('Could not apply thresholds.');$('save-status').textContent='Applied for this reader session.';}catch(e){$('save-status').textContent=e.message;}
};
function warnAudio(data){
  // Aggregate concurrent warnings; never queue a backlog of spoken messages.
  const events=data.events.filter(e=>!seen.has(e.id));for(const e of data.events)seen.add(e.id);
  if(seen.size>256)seen=new Set(data.events.map(e=>e.id));
  if(events.length&&armed&&!muted)pendingVoice={keys:events.map(e=>e.key),at:performance.now()};
  if(pendingVoice&&performance.now()-lastSound>5000){
    const keys=pendingVoice.keys.filter(k=>data.active.includes(k));
    if(keys.length&&performance.now()-pendingVoice.at<10000){sound(keys.map(k=>`${names[k]} low. ${Math.round(data.values[k])} percent.`).join(' '));lastSound=performance.now();}
    pendingVoice=null;
  }
}

const pageTitles = {overview:'Overview', player:'Player', flight:'Flight Deck', systems:'Ship Systems', log:'Event Log'};
let currentPage = 'overview';
let historySeconds = 30;
let dashboard = null;
let scenarioMenuReady = false;
const listCache = new Map();
let playback = null, requestEpoch = 0, homeMode = 'live';
let markSignature = null, openingReplay = false, seeking = false;

const themeKey = 'sc-cockpit.theme';
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
let preference;
try { preference = localStorage.getItem(themeKey); } catch { /* Storage can be disabled. */ }
if (!['light', 'dark'].includes(preference)) preference = null;

function applyTheme() {
  const theme = preference || (systemTheme.matches ? 'dark' : 'light');
  document.documentElement.dataset.theme = theme;
  for (const name of ['light', 'dark']) {
    $('theme-' + name).setAttribute('aria-pressed', String(name === theme));
  }
  drawHistory();
}
for (const name of ['light', 'dark']) {
  $('theme-' + name).onclick = () => {
    preference = name;
    try { localStorage.setItem(themeKey, name); } catch { /* Applies for this session. */ }
    applyTheme();
  };
}
systemTheme.addEventListener('change', applyTheme);
applyTheme();

function setPage(page) {
  currentPage = page;
  for (const button of document.querySelectorAll('[data-page]')) {
    const selected = button.dataset.page === page;
    $(button.dataset.page).hidden = !selected;
    button.setAttribute('aria-pressed',String(selected));
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  document.title = pageTitles[page] + ' · SC Cockpit';
  if (page === 'flight') drawHistory();
}
for (const button of document.querySelectorAll('[data-page]')) {
  button.addEventListener('click', () => setPage(button.dataset.page));
}
for (const button of document.querySelectorAll('[data-window]')) {
  button.addEventListener('click', () => {
    historySeconds = Number(button.dataset.window);
    for (const tab of document.querySelectorAll('[data-window]')) {
      tab.setAttribute('aria-pressed', String(tab === button));
    }
    drawHistory();
  });
}

function textNode(tag, content) {
  const node = document.createElement(tag);
  node.textContent = content;
  return node;
}
function reading(value, suffix='') {
  if (value == null) return '—';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—';
    return value.toLocaleString(undefined, {maximumFractionDigits: 2}) + suffix;
  }
  return String(value);
}
// Keep the decoded enum alongside the condition, including intermediate deployment states.
function light(label, exact, trueStates, freshness='') {
  const known = exact != null;
  const state = known ? String(trueStates.includes(exact)) : 'unavailable';
  const detail = known ? state + ' (' + exact + ')' : 'Unavailable';
  const description = label + ': ' + detail + (known && freshness ? ' · ' + freshness : '');
  const node = document.createElement('span');
  node.className = 'indicator-light';
  node.dataset.state = state;
  node.setAttribute('role', 'img');
  node.setAttribute('aria-label', description);
  node.title = description;
  node.tabIndex = 0;
  return node;
}
function condition(label, exact, trueStates=['On'], freshness='') {
  // The section stamp carries the changing age. Keep focused lights stable as it ticks.
  const ageLabel = freshness.startsWith('Last known') ? 'Last known' : freshness;
  return [label, exact, '', '', trueStates, ageLabel];
}
// A reported field can have a missing current value (null). A field the
// reader has never received has no key in the display model (undefined).
function reportedReadings(current = {}, reported = {}) {
  const result = {...reported};
  for (const [key, value] of Object.entries(current)) {
    if (key === 'items') continue;
    if (value && typeof value === 'object') {
      result[key] = reportedReadings(value, reported[key]);
    } else if (value != null && value !== '') {
      result[key] = value;
    }
  }
  if (current.items || reported.items) {
    const items = current.items?.length ? current.items : Object.keys(reported.items || {}).map(name => ({name}));
    result.items = items.map(item => reportedReadings(item, reported.items?.[item.name]));
  }
  return result;
}
function rows(id, candidates, big=false, telemetry=false) {
  const entries = candidates.filter(([, value]) =>
    (value === null && telemetry) || (value != null && value !== '' && (typeof value !== 'number' || Number.isFinite(value))));
  const signature = JSON.stringify(entries);
  if (listCache.get(id) === signature) return;
  listCache.set(id, signature);
  const nodes = entries.map(([label, value, unit='', note='', trueStates, freshness]) => {
    const row = document.createElement('div');
    row.className = big ? 'metric' : 'row';
    const title = textNode('span', label);
    const result = trueStates ? light(label, value, trueStates, freshness) : textNode('strong', reading(value, big ? '' : unit));
    row.dataset.reading = label;
    if (value == null && !trueStates) {
      result.title = 'No current reading';
      result.setAttribute('aria-label', label + ': no current reading');
    }
    if (big) {
      title.className='metric-label';
      const line=document.createElement('div');
      line.className='reading';
      line.append(result);
      if (unit && value != null) {
        const suffix = textNode('span', unit.trim());
        suffix.className = 'unit';
        line.append(suffix);
      }

      row.append(title,line);
    } else {
      row.append(title,result);
    }
    if (note && value != null) {
      const annotation=textNode('small',note);
      annotation.className='metric-note';
      row.append(annotation);
    }
    if (!big && unit === '%' && Number.isFinite(value)) {
      const bar=document.createElement('progress');
      bar.max=100; bar.value=value;
      bar.setAttribute('aria-label',label);
      row.append(bar);
    }
    return row;
  });
  $(id).replaceChildren(...nodes);
}
function telemetryRows(id, entries, big=false) {
  rows(id, entries, big, true);
}
function gauge(id, value, freshness) {
  const node = $(id + '-gauge');
  node.hidden = value === undefined;
  if (node.hidden) return;
  const text = reading(value, '%');
  $(id + '-value').textContent = text;
  const label = id === 'health' ? 'Health' : 'Oxygen';
  if (value == null) {
    node.removeAttribute('aria-valuemin');
    node.removeAttribute('aria-valuemax');
    node.removeAttribute('aria-valuenow');
    node.setAttribute('role', 'group');
    node.setAttribute('aria-label', label + ': no current reading');
    node.removeAttribute('aria-valuetext');
    $(id + '-arc').setAttribute('visibility', 'hidden');
    return;
  }
  node.setAttribute('role', 'meter');
  node.setAttribute('aria-label', label);
  node.setAttribute('aria-valuemin', '0');
  node.setAttribute('aria-valuemax', '100');
  node.setAttribute('aria-valuenow', String(value));
  node.setAttribute('aria-valuetext', text + ' · ' + freshness);
  const arc = $(id + '-arc');
  arc.setAttribute('stroke-dasharray', Math.min(100,Math.max(0,value)) + ' 100');
  arc.setAttribute('visibility', value > 0 ? 'visible' : 'hidden');
}
function drawHistory() {
  if (currentPage !== 'flight') return;
  const canvas = $('history');
  const width = canvas.clientWidth || 280, height = 130;
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.scale(scale,scale);
  const colors = getComputedStyle(canvas);
  ctx.strokeStyle = colors.borderTopColor;
  for (let y=10; y<=120; y+=22) {
    ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(width,y); ctx.stroke();
  }
  const points = (dashboard?.history || []).filter(([age]) => age <= historySeconds);
  const values = points.map(p=>p[1]).filter(Number.isFinite);
  $('history-label').textContent = values.length ? ((latest?.original_mode || latest?.mode) === 'demo' ? 'Speed · m/s · last ' : 'Speed · native units · last ') + historySeconds + ' seconds' : 'Waiting for speed samples';
  if (!values.length) return;
  const max = Math.max(1, ...values);
  ctx.strokeStyle=colors.color; ctx.fillStyle=ctx.strokeStyle; ctx.lineWidth=2; ctx.beginPath();
  let previousAge = null;
  for (const [age,value] of points) {
    if (!Number.isFinite(value)) { previousAge = null; continue; }
    const x = width*(1-age/historySeconds), y = 120-108*value/max;
    if (previousAge === null || previousAge-age > 5) { ctx.moveTo(x,y); ctx.fillRect(x-1,y-1,2,2); }
    else ctx.lineTo(x,y);
    previousAge = age;
  }
  ctx.stroke();
}
window.addEventListener('resize',drawHistory);
function renderLog() {
  const monitor = dashboard?.kill_monitor;
  const report = [];
  const telemetry = monitor?.last_telemetry_report;
  if (telemetry) {
    report.push(['Latest telemetry report', telemetry.value, '', new Date(telemetry.time).toLocaleString()]);
  }
  const death = monitor?.last_death;
  if (death) {
    report.push(['Latest death in Game.log', death.value, '', new Date(death.time).toLocaleString()]);
  }
  $('overview-kill-panel').hidden = report.length === 0;
  rows('overview-kill-report', report);
  const info = [['Game log', monitor?.status || 'Unavailable'], ...report];
  if (!report.length && monitor?.status === 'Watching Game.log') {
    info.push(['Latest report', 'Waiting for a death or downing']);
  }
  rows('kill-monitor', info);
  const activity = dashboard?.activity;
  $('activity-panel').hidden = !activity;
  if (activity) {
    $('activity-source').textContent = monitor?.status || 'Game.log';
    const summary = [];
    for (const mission of activity.missions) {
      summary.push(['Contract', mission.name], ...mission.objectives.map(value=>['Objective',value]));
    }
    if (!activity.missions.length) summary.push(['Contract', 'No active contract reported']);
    summary.push(['Ship kills credited to you',activity.kills]);
    if (activity.last_kill) {
      const kill = activity.last_kill;
      summary.push(['Latest ship kill',kill.ship,'',kill.cause + ' · ' + new Date(kill.time).toLocaleTimeString()]);
    }
    for (const [key,zone] of Object.entries(activity.zones)) {
      const when = 'Last reported · ' + new Date(zone.time).toLocaleTimeString();
      if (key === 'Armistice' || key === 'Monitored space') {
        const entry = condition(key === 'Armistice' ? 'Inside armistice zone' : 'Inside monitored space',zone.value,['Inside'],when);
        entry[3] = when;
        summary.push(entry);
      } else summary.push([key,zone.value,'',when]);
    }
    rows('activity-summary',summary);
  }
  const performance = dashboard?.performance;
  $('performance-panel').hidden = !performance;
  if (performance) rows('performance-rows',[
    ['Source',performance.file,'',new Date(performance.time).toLocaleString()], ...Object.entries(performance.rows)]);
  const journal = dashboard?.journal || [];
  const category = $('event-category').value;
  renderEvents('entries', journal.filter(event => (!category || event.category === category) &&
    (!$('warn-filter').checked || event.warning)));
  renderEvents('recent-events', journal.slice(0, 3));
}
function renderEvents(id, entries) {
  const signature = JSON.stringify(entries);
  if (listCache.get(id) === signature) return;
  listCache.set(id, signature);
  const lines = entries.map(entry => {
    const line = document.createElement('li');
    const stamp = textNode('time',new Date(entry.time).toLocaleTimeString());
    stamp.dateTime=entry.time;
    const config = configuration[entry.label.toLowerCase()];
    const indicator = config || (powerLabels.includes(entry.label) ? [powerCondition(entry.label),['On']] :
      assistLabels.includes(entry.label) ? [entry.label + ' active',['On']] :
      warnings.includes(entry.label) ? [warningLabel(entry.label) + ' active',warningStates] : null);
    line.append(stamp,textNode('strong',indicator ? indicator[0] : entry.label),
      indicator ? light(indicator[0], entry.value, indicator[1]) : textNode('span',entry.value));
    if (entry.warning) line.className='warning';
    return line;
  });
  $(id).replaceChildren(...(lines.length ? lines : [textNode('li','No matching events yet')]));
}
$('open-log').onclick=()=>setPage('log');
$('warn-filter').onchange=renderLog;
$('event-category').onchange=renderLog;
async function changeScenario(value) {
  try {
    const response=await fetch('/api/scenario',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
    if (!response.ok) throw new Error();
  } catch { $('connection').textContent='Demo scenario change failed'; }
};
$('scenario').onchange=e=>changeScenario(e.target.value);
$('pause').onclick=()=>changeScenario(dashboard?.feed === 'paused' ? 'Resume' : 'Pause');
$('source').onclick = () => {
  $('feed-dialog').showModal();
  if (latest) renderDiagnostics(latest);
  loadRecordings().catch(showSessionError);
};
const configuration = {
  gear:['Gear deployed',['Deployed']], vtol:['VTOL deployed',['Deployed']],
  transformation:['Transformation deployed',['Deployed']], landed:['Landed',['Landed']],
  doors:['Doors closed',['Closed']], locks:['Doors locked',['Locked']], lights:['Exterior lights on',['On']]
};
const powerLabels = ['Power plants','Engines','Weapons power','Coolers','Life support','Gravity','Sensors','Shield generators','Shield emitters'];
const assistLabels = ['Coupled flight','ESP','G-safe','Gravity compensation','Cruise','Speed limiter','Proximity assist','Stability assist','Wind compensation','Landing assist','IFCS core','Command behaviour'];
const warnings = ['Power warning','Weapons warning','Thruster warning','Shield warning','Cooling warning','Quantum drive','Radar warning','Proximity','Hit warning','Missile warning'];
const warningStates = ['Minor · flashing','Minor','Major · flashing','Major'];
function powerCondition(name) {
  return name === 'Weapons power' ? 'Weapons powered' : name + ' powered';
}
function warningLabel(name) {
  return name.endsWith('warning') ? name : name + ' warning';
}
function statusText(feed) {
  return {waiting:'Waiting for first sample', live:'Receiving telemetry', stale:'Stale · last known readings',
          paused:'Demo paused · frozen sample', disconnected:'Disconnected'}[feed] || 'Reader update required';
}
function sectionStatus(data, key) {
  if (!data.dashboard.sections[key]) return data.dashboard.feed === 'disconnected' ? 'Disconnected' : 'Unavailable';
  if (data.dashboard.feed === 'paused') return 'Frozen sample';
  const age = data.ages[key];
  return age >= 5 ? 'Last known · ' + Math.floor(age) + ' s ago' : 'Current';
}
function render(data) {
  latest=data;
  dashboard=data.dashboard;
  if (!dashboard) throw new Error('Restart the cockpit reader to load the dashboard adapter.');
  const replay = data.mode === 'replay';
  const demo = (data.original_mode || data.mode) === 'demo';
  $('mode').textContent=replay ? 'REPLAY' : demo?'DEMO · SIMULATED':'LIVE SOURCE';
  $('connection').textContent=statusText(dashboard.feed);
  if (demo && dashboard.feed==='live' && dashboard.scenario==='Partial feed') {
    $('connection').textContent='Partial sample';
  }
  $('connection').dataset.partial = String(demo && dashboard.scenario === 'Partial feed');
  $('source').dataset.feed=dashboard.feed==='disconnected'?'offline':dashboard.feed;
  $('feed-detail').textContent=(replay ? 'Replay · recorded ' + data.original_mode + ' feed. ' : demo?'Local simulation. ':'Star Citizen · 127.0.0.1:2942. ') + statusText(dashboard.feed) + '. ' + (data.detail || '');
  $('pause').textContent=dashboard.feed==='paused'?'Resume demo':'Pause demo';
  $('pause').disabled=!['live','paused'].includes(dashboard.feed);
  $('connection').title = data.detail || statusText(dashboard.feed);
  $('simulation').hidden=!demo || replay;
  $('rules').querySelector?.('button[type=submit]')?.toggleAttribute('disabled', replay);
  if (demo && !replay && !scenarioMenuReady) {
    for (const name of dashboard.scenarios) $('scenario').append(new Option(name,name));
    scenarioMenuReady=true;
  }
  if (demo && document.activeElement !== $('scenario')) $('scenario').value=dashboard.scenario;
  const sections = dashboard.sections;
  const known = dashboard.reported || {};
  const a = reportedReadings(sections.actor, known.actor);
  const inShip = !['On foot','EVA'].includes(a.context);
  const v = reportedReadings(sections.vehicle, inShip ? known.vehicle : {});
  const w = reportedReadings(sections.weapons, inShip ? known.weapons : {});
  const shipName = v.name || v.class || (['On foot','EVA'].includes(a.context) ? 'No active ship' : 'Awaiting vessel telemetry');
  $('flight-subtitle').textContent = $('overview-ship').textContent = shipName;
  const actorStatus = sectionStatus(data,'actor'), shipStatus = sectionStatus(data,'vehicle');
  const weaponStatus = sectionStatus(data,'weapons');
  $('actor-feed').textContent = $('overview-actor-feed').textContent = actorStatus;
  $('systems-feed').textContent = $('flight-feed').textContent = $('overview-flight-feed').textContent = shipStatus;
  $('weapons-feed').textContent = weaponStatus;

  const native = 'Native · unit unverified';
  const primary = [['Health',a.health,'%'],['Oxygen',a.oxygen,'%'],
    ['Heart rate',a.heart,demo?' bpm':'',demo?'':native],
    ['Body temperature',a.temperature,demo?' °C':'',demo?'':native]];
  const reserves = [['Hydration',a.hydration,'%'],['Nutrition',a.nutrition,'%']];
  const activity = [['Player context',a.context],condition('Atmosphere breathable',a.air,['Breathable'],actorStatus)];
  $('overview-seat').textContent = a.context === 'Seated' && a.role ? a.role + ' seat' : a.context || '';
  $('overview-actor-feed').dataset.current = String(actorStatus === 'Current');
  $('overview-flight-feed').dataset.current = String(shipStatus === 'Current');
  $('sample-time').textContent = data.last_sample_at == null ? '' :
    'Last sample ' + new Date(data.last_sample_at * 1000).toLocaleTimeString();
  gauge('health', a.health, actorStatus);
  gauge('oxygen', a.oxygen, actorStatus);
  telemetryRows('primaries', primary.slice(2), true);
  telemetryRows('suit-reserves', reserves);
  telemetryRows('player-context', activity);
  telemetryRows('support', [...primary,...reserves,['Intoxication level',a.intoxication,'',a.intoxication == null?'':native]]);
  telemetryRows('context', [...activity,['Credit balance (UEC)',a.uec_balance == null ? a.uec_balance : BigInt(a.uec_balance).toLocaleString()],
    ['Crew role',a.role],['Seat',a.seat],
    ...[['Pressure',a.pressure],['Gravity',a.gravity],['External temperature',a.external_temperature],
      ['Ambient radiation rate',a.radiation]].map(([label,value])=>[label,value,'',value == null?'':native])]);
  const f=v.flight || {}, c=v.configuration || {};
  const motion = [['Speed',f.speed,'m/s'],['Radar altitude',f.radar_altitude,'m'],
    ['Vertical speed',f.vertical_speed_indicator,'m/s'],['Heading',f.heading,'°']];
  telemetryRows('motion',motion.map(([label,value,unit])=>[label,value,demo?unit:'',demo?'':native]),true);
  const hydrogen = f.hydrogen_percent;
  const hydrogenNote = f.hydrogen_m != null ? f.hydrogen_m.toFixed(2) + 'M / 53.20M'
    : hydrogen != null ? 'Tank remaining' : 'Raw telemetry · scale unverified';
  const quantum = demo ? f.quantum_percent : f.quantum_fuel;
  const quantumNote = demo ? 'Simulated' : quantum === 0 ? 'Raw zero · tank level unconfirmed' : 'Raw telemetry · HUD units unverified';
  telemetryRows('reserves',[['Hydrogen fuel',hydrogen ?? f.hydrogen_fuel,hydrogen != null ? '%' : '',hydrogenNote],
    ['Quantum fuel',demo || quantum == null ? quantum : String(quantum),demo?'%':'',quantumNote],
    ['Boost reserve',demo ? f.boost_percent : f.boost,demo?'%':'',demo?'Simulated':'Native reading']]);
  const configRow = key => condition(configuration[key][0],c[key],configuration[key][1],shipStatus);
  telemetryRows('flight-config',['gear','vtol','doors','lights'].map(configRow));
  telemetryRows('sys-config',Object.keys(configuration).map(configRow));
  telemetryRows('power',powerLabels.map(name=>condition(powerCondition(name),v.power?.[name],['On'],shipStatus)));
  telemetryRows('indicators',warnings.map(name=>condition(warningLabel(name),v.annunciators?.[name],warningStates,shipStatus)));
  telemetryRows('assists',assistLabels.map(name=>condition(name + ' active',v.assists?.[name],['On'],shipStatus)));
  const preset = [['Preset index',w.preset_index]];
  preset.push(['Preset name',w.preset]);
  const selection = (w.items || []).map(item =>
    condition(item.name + ' in preset',item.selected,['On'],weaponStatus));
  const gunnerRows = [...preset,condition('Staggered fire enabled',w.staggered,['On'],weaponStatus),...selection];
  $('gunner-panel').hidden = a.context !== 'Seated' || a.role !== 'Gunner';
  if (!$('gunner-panel').hidden) {
    $('gunner-feed').textContent = 'Weapons · ' + weaponStatus;
    telemetryRows('gunner-weapons', [['Seat',a.seat,'',actorStatus],...gunnerRows]);
  }
  telemetryRows('armament',[...gunnerRows,condition('Gimbals locked',w.gimbals,['On'],weaponStatus),
    ...(w.items || []).flatMap(item=>[condition(item.name + ' ready to fire',item.ready,['On'],weaponStatus),
      condition(item.name + ' powered',item.power,['On'],weaponStatus)])]);
  $('warnings').textContent=data.active.length?data.active.map(k=>names[k]+' low').join(' · '):
    $('actor-feed').textContent==='Current' ? 'No active vital warnings' : 'Awaiting current vitals';
  $('raw').textContent=JSON.stringify(data.frame,null,2);
  if (!hydrated && data.rules) {
    for(const key of Object.keys(names)) {
      $('enabled-'+key).checked=data.rules[key].enabled;
      $('threshold-'+key).value=data.rules[key].threshold;
    }
    hydrated=true;
  }
  if (!replay && dashboard.feed==='live' && data.events) warnAudio(data);
  else { pendingVoice=null; window.speechSynthesis?.cancel(); }
  $('history-panel').hidden = f.speed === undefined && !dashboard.history?.some(([, speed]) => Number.isFinite(speed));
  drawHistory(); renderLog(); renderSession(data);
}
function displaySample(sample) {
  try {
    render(sample);
  } catch (error) {
    // Keep the received frame available for inspection when a view fails.
    $('connection').textContent = 'Dashboard display error';
    $('source').dataset.feed = 'stale';
    $('connection').title = $('feed-detail').textContent =
      'The reader responded. Some displayed readings may be from the previous update. ' + error.message;
    $('raw').textContent = JSON.stringify(sample.frame ?? {}, null, 2);
    pendingVoice = null;
  }
}
function seconds(value) {
  return value == null ? '—' : Number(value).toFixed(1) + ' s';
}
function renderDiagnostics(data) {
  const diag = data.diagnostics;
  const meta = diag?.metadata || {};
  $('diag-metadata').textContent = [data.mode === 'replay' ? 'Replay' : data.mode === 'demo' ? 'Demo' : 'Live',
    'Build ' + (meta.build || 'unknown'), 'Channel ' + (meta.channel || 'unknown'),
    'Last packet ' + (diag?.packet_age == null ? 'unknown' : seconds(diag.packet_age) + ' ago')].join(' · ');
  const filter = $('diag-filter').value.trim().toLowerCase();
  function table(id, entries, format) {
    const nodes = [];
    for (const [path, row] of Object.entries(entries || {})) {
      if (!path.toLowerCase().includes(filter)) continue;
      const tr = document.createElement('tr');
      for (const value of [path, ...format(row)]) {
        tr.append(textNode('td', value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value)));
      }
      nodes.push(tr);
    }
    $(id).replaceChildren(...nodes);
  }
  table('diag-fields', diag?.fields, ([value, unit, age, status]) => [value, unit, seconds(age), status]);
  if ($('diag-input-panel').open) {
    table('diag-inputs', diag?.inputs, ([delivery, mapping, value, age]) => [delivery, mapping, value, seconds(age)]);
  }
}
function renderSession(data) {
  const recording = Boolean(data.recording);
  $('recording-lamp').hidden = !recording;
  $('record-start').disabled = recording || !!playback;
  $('record-mark').disabled = $('record-stop').disabled = !recording || !!playback;
  $('record-open').disabled = recording || !$('record-files').value;
  $('replay-tools').hidden = !playback;
  if (data.recording) $('recording-lamp').title = data.recording;
  if (data.recording_error) $('record-feedback').textContent = data.recording_error;
  if (playback && data.replay) {
    const r = data.replay;
    playback.duration = r.duration;
    if (r.position >= r.duration) {
      playback.position = r.duration;
      playback.playing = false;
    }
    $('replay-play').textContent = playback.playing ? 'Pause' : 'Play';
    $('replay-seek').max = r.duration;
    if (!seeking) $('replay-seek').value = r.position;
    $('replay-time').textContent = seconds(r.position) + ' / ' + seconds(r.duration);
    $('replay-return').textContent = homeMode === 'demo' ? 'Return to Demo' : 'Return to Live';
    const signature = JSON.stringify(r.bookmarks);
    if (markSignature !== signature) {
      $('replay-marks').replaceChildren(new Option('Jump to bookmark', ''),
        ...r.bookmarks.map(([at,label]) => new Option(label + ' · ' + seconds(at), String(at))));
      markSignature = signature;
    }
    $('replay-marks').disabled = !r.bookmarks.length;
  }
  if ($('feed-dialog').open) renderDiagnostics(data);
}
$('diag-filter').oninput = $('diag-input-panel').ontoggle = () => { if (latest) renderDiagnostics(latest); };
function showSessionError(error) { $('record-feedback').textContent = error.message; }
async function sessionJSON(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Session request failed');
  return result;
}
async function loadRecordings() {
  const files = await sessionJSON('/api/recordings');
  const selected = $('record-files').value;
  $('record-files').replaceChildren(...files.map(file => new Option(file, file)));
  if (files.includes(selected)) $('record-files').value = selected;
  $('record-open').disabled = !files.length || !!latest?.recording;
}
for (const [id, action] of [['record-start','start'],['record-stop','stop'],['record-mark','bookmark']]) {
  $(id).onclick = async () => {
    try {
      $(id).disabled = true;
      const result = await sessionJSON('/api/recording', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({action, label:$('mark-label').value})});
      $('record-feedback').textContent = {start:'Recording started.',stop:'Recording saved.',bookmark:'Bookmark added.'}[action];
      if (action === 'bookmark') $('mark-label').value = '';
      await refresh();
      if (action === 'stop') {
        await loadRecordings();
        $('record-files').value = result.saved;
      }
    } catch (error) { showSessionError(error); }
  };
}
function positionNow() {
  return playback.position + (playback.playing ? (performance.now() - playback.started) / 1000 : 0);
}
function setPosition(position, playing) {
  playback.position = Math.min(playback.duration, Math.max(0,position));
  playback.playing = playing;
  playback.started = performance.now();
  return refresh();
}
$('record-open').onclick = async () => {
  const file = $('record-files').value;
  if (!file || latest?.recording || openingReplay) return;
  openingReplay = true;
  try {
    const epoch = ++requestEpoch;
    const sample = await sessionJSON('/api/replay?file=' + encodeURIComponent(file) + '&at=0');
    if (epoch !== requestEpoch) return;
    playback = {file, position:0, duration:sample.replay.duration, playing:false, started:performance.now()};
    markSignature = null;
    $('feed-dialog').close();
    displaySample(sample);
  } catch (error) { showSessionError(error); } finally { openingReplay = false; }
};
$('replay-play').onclick = () => setPosition(positionNow() >= playback.duration ? 0 : positionNow(), !playback.playing);
$('replay-seek').oninput = () => { seeking = true; };
$('replay-seek').onchange = e => {
  seeking = false;
  return setPosition(Number(e.target.value), playback.playing);
};
$('replay-marks').onchange = e => {
  if (e.target.value !== '') setPosition(Number(e.target.value), playback.playing);
  e.target.value = '';
};
$('replay-return').onclick = () => {
  playback = null;
  latest = dashboard = null;
  $('replay-tools').hidden = true;
  return refresh();
};
async function refresh() {
  if (openingReplay) return;
  const epoch = ++requestEpoch;
  try {
    const url = playback ? '/api/replay?file=' + encodeURIComponent(playback.file) + '&at=' + positionNow() : '/api/state';
    const sample = await sessionJSON(url, {signal:AbortSignal.timeout(3500)});
    if (epoch !== requestEpoch) return;
    if (!playback) homeMode = sample.mode;
    displaySample(sample);
  } catch (error) {
    if (epoch !== requestEpoch) return;
    if (playback) {
      playback.position = Math.min(positionNow(), playback.duration);
      playback.playing = false;
      $('connection').textContent = 'Replay unavailable';
      $('replay-play').textContent = 'Play';
      return;
    }
    render({mode:homeMode, dashboard:{feed:'disconnected',sections:{},reported:dashboard?.reported,scenarios:[],journal:dashboard?.journal||[],history:[],
      activity:dashboard?.activity,performance:dashboard?.performance,
      kill_monitor:{...dashboard?.kill_monitor,status:'Reader disconnected'}},ages:{},frame:{},active:[],detail:error.message});
  }
}
async function poll() {
  await refresh();
  setTimeout(poll,500);
}
audioStatus();
setPage('overview');
poll();
