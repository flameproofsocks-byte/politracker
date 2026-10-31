// Video brief editor: live script length meter, bill pickers, draft buttons, save. Plain JS.
(function () {
  const app = document.getElementById('video-app'); if (!app) return;
  const $ = function (id) { return document.getElementById(id); };
  const wps = parseFloat(app.dataset.wps), lo = +app.dataset.lo, hi = +app.dataset.hi, maxPicks = +app.dataset.max;
  const blurb = $('blurb'), script = $('script'), hook = $('hook'), outro = $('outro'), status = $('save-status');
  const HEADERS = {'Content-Type': 'application/json', 'X-Politracker': '1'};
  let dirty = false, autoBlurb = blurb.value;                                  // autoBlurb = last text we generated (so we only confirm before overwriting edits)

  function words(text) { return (text.match(/[\p{L}\p{N}_'’-]+/gu) || []).length; }
  function picks(side) {
    return Array.from(document.querySelectorAll('input[data-side="' + side + '"]:checked')).map(function (el) {
      const i = el.value.indexOf('|'); return [parseInt(el.value.slice(0, i), 10), el.value.slice(i + 1)];
    });
  }
  function limitPicks() {
    ['for', 'against'].forEach(function (side) {
      const boxes = Array.from(document.querySelectorAll('input[data-side="' + side + '"]'));
      const full = boxes.filter(function (b) { return b.checked; }).length >= maxPicks;
      boxes.forEach(function (b) { b.disabled = full && !b.checked; });
    });
  }
  function meter() {
    const n = words(script.value), secs = Math.round(n / wps), scale = 45;
    const fill = $('meter-fill'), text = $('meter-text'), band = document.querySelector('.meter .band');
    band.style.left = (lo / scale * 100) + '%'; band.style.width = ((hi - lo) / scale * 100) + '%';
    fill.style.width = Math.min(100, secs / scale * 100) + '%';
    let state = 'good', note = 'on target';
    if (n === 0) { state = 'empty'; note = 'no script yet'; }
    else if (secs < lo) { state = 'short'; note = 'too short: about ' + (Math.ceil(lo * wps) - n) + ' more words'; }
    else if (secs > hi) { state = 'long'; note = 'too long: trim about ' + (n - Math.floor(hi * wps)) + ' words'; }
    fill.className = state;
    text.textContent = n + ' words · about ' + secs + 's (' + lo + '–' + hi + 's target) · ' + note;
  }
  function mark() { dirty = true; status.textContent = 'Unsaved changes'; }
  async function post(path, body) {
    const r = await fetch('/videos/' + app.dataset.member + path, {method: 'POST', headers: HEADERS, body: JSON.stringify(body)});
    let data = {}; try { data = await r.json(); } catch (e) { /* non-JSON error page */ }
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }
  async function save() {
    status.textContent = 'Saving…';
    try {
      const d = await post('/save', {blurb: blurb.value, script: script.value, hook: hook.value, outro: outro.value, 'for': picks('for'), against: picks('against')});
      dirty = false; status.textContent = 'Saved ' + new Date(d.updated_at).toLocaleTimeString();
    } catch (e) { status.textContent = 'Not saved: ' + e.message; }
  }
  async function draft() { return post('/draft', {'for': picks('for'), against: picks('against')}); }

  $('save').addEventListener('click', save);
  document.addEventListener('keydown', function (e) { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); } });
  [blurb, script, hook, outro].forEach(function (el) { el.addEventListener('input', mark); });
  script.addEventListener('input', meter);
  document.querySelectorAll('input[data-side]').forEach(function (b) { b.addEventListener('change', function () { limitPicks(); mark(); }); });
  $('rebuild').addEventListener('click', async function () {
    if (blurb.value.trim() !== autoBlurb.trim() && !confirm('Replace the blurb you edited with a fresh one built from the ticked bills?')) return;
    try { const d = await draft(); blurb.value = autoBlurb = d.blurb; mark(); } catch (e) { status.textContent = 'Could not rebuild: ' + e.message; }
  });
  $('outline').addEventListener('click', async function () {
    if (script.value.trim() && !confirm('Replace the current script with the starter outline?')) return;
    try { const d = await draft(); script.value = d.outline; meter(); mark(); } catch (e) { status.textContent = 'Could not build the outline: ' + e.message; }
  });
  $('frames').addEventListener('click', function () {
    status.textContent = 'Rendering the slides\u2026 the download starts in about 20 seconds (unsaved edits are not included).';
  });
  window.addEventListener('beforeunload', function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  limitPicks(); meter();
})();
