// politracker UI behaviour. Plain JS, no build step.

// ---- Thumbs: each bill has an approve (up) and deny (down) button. Clicking the active one clears it.
// Every thumbs control for the same bill on the page is updated together.
const THUMB_TITLES = {up: ['Approve this bill', 'You approve of this bill (click to clear)'],
                      down: ['Deny this bill', 'You deny this bill (click to clear)']};
document.addEventListener('click', async function (e) {
  const btn = e.target.closest('.thumb'); if (!btn) return;
  const box = btn.closest('.thumbs'); if (!box || !box.dataset.bill) return;
  const c = box.dataset.congress, b = box.dataset.bill, target = btn.dataset.to;
  const next = box.dataset.stance === target ? 'none' : target;
  const r = await fetch('/bill/' + c + '/' + encodeURIComponent(b) + '/star?to=' + next, {method: 'POST', headers: {'X-Politracker': '1'}});
  if (!r.ok) return;
  const stance = (await r.json()).stance || '';
  document.querySelectorAll('.thumbs').forEach(function (t) {
    if (t.dataset.congress !== c || t.dataset.bill !== b) return;
    t.dataset.stance = stance;
    t.querySelectorAll('.thumb').forEach(function (x) {
      const on = x.dataset.to === stance;
      x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false');
      x.title = THUMB_TITLES[x.classList.contains('up') ? 'up' : 'down'][on ? 1 : 0];
    });
  });
});

// ---- Summary cells are clamped to 3 lines; click to expand/collapse.
document.addEventListener('click', function (e) {
  const el = e.target.closest('.brief .clamp'); if (el) el.classList.toggle('open');
});

// ---- Home page: live member search (input[data-suggest] -> /api/members). Arrow keys + Enter work too.
document.querySelectorAll('input[data-suggest]').forEach(function (input) {
  const box = document.getElementById(input.dataset.suggest); if (!box) return;
  let timer = null, items = [], sel = -1, seq = 0;
  const close = function () { box.hidden = true; box.textContent = ''; items = []; sel = -1; };
  const mark = function () { items.forEach(function (a, i) { a.classList.toggle('sel', i === sel); }); };
  input.addEventListener('input', function () {
    clearTimeout(timer);
    const term = input.value.trim();
    if (term.length < 2) { close(); return; }
    timer = setTimeout(async function () {
      const mine = ++seq;
      const r = await fetch('/api/members?q=' + encodeURIComponent(term));
      if (!r.ok || mine !== seq) return;                       // ignore stale responses
      const people = await r.json();
      box.textContent = ''; items = []; sel = -1;
      people.forEach(function (p) {
        const a = document.createElement('a'); a.href = (input.dataset.link || '/politician/') + encodeURIComponent(p.id);   // data-link lets other pages reuse this search box
        const name = document.createElement('b'); name.textContent = p.name;
        const party = document.createElement('span'); party.className = 'chip ' + p.party; party.textContent = p.party || '?';
        const sub = document.createElement('span'); sub.className = 'sub'; sub.textContent = p.chamber + ' · ' + p.state;
        a.append(name, party, sub); box.append(a); items.push(a);
      });
      box.hidden = items.length === 0;
    }, 150);
  });
  input.addEventListener('keydown', function (e) {
    if (box.hidden || !items.length) return;
    if (e.key === 'ArrowDown') { sel = (sel + 1) % items.length; mark(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { sel = (sel - 1 + items.length) % items.length; mark(); e.preventDefault(); }
    else if (e.key === 'Enter' && sel >= 0) { window.location = items[sel].href; e.preventDefault(); }
    else if (e.key === 'Escape') close();
  });
  document.addEventListener('click', function (e) { if (!box.contains(e.target) && e.target !== input) close(); });
});

// ---- Portraits are hotlinked from a public image mirror; hide the frame if one is missing (error events don't bubble, so capture).
document.addEventListener('error', function (e) {
  if (e.target && e.target.matches && e.target.matches('img.portrait')) e.target.hidden = true;
}, true);
