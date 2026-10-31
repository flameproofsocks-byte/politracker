// Vertical presenter: scales the 1080x1920 stage to the window, plays the slides in order, and handles the keys.
// URL options: ?scene=N (start slide, 1-based)  ?auto=0 (no auto-advance)  ?loop=1  ?clean=1 (hide controls/progress)  ?freeze=1 (no animation, for image export)
(function () {
  const stage = document.getElementById('stage');
  const scenes = Array.from(document.querySelectorAll('.scene'));
  const bars = Array.from(document.querySelectorAll('.progress i'));
  const where = document.getElementById('where'), playBtn = document.getElementById('play');
  const body = document.body, loop = body.dataset.loop === '1';
  let idx = Math.max(0, Math.min(scenes.length - 1, (parseInt(body.dataset.start, 10) || 1) - 1));
  let playing = body.dataset.auto === '1', timer = null, startedAt = 0, remaining = 0;

  function fit() {
    const s = Math.min(window.innerWidth / 1080, window.innerHeight / 1920);
    stage.style.transform = 'scale(' + s + ')';
    stage.style.left = (window.innerWidth - 1080 * s) / 2 + 'px';
    stage.style.top = (window.innerHeight - 1920 * s) / 2 + 'px';
  }
  function seconds(i) { return parseFloat(scenes[i].dataset.seconds) || 3; }
  function render() {
    scenes.forEach(function (el, k) { el.classList.toggle('on', k === idx); });
    bars.forEach(function (b, k) {
      b.classList.remove('active', 'done');
      if (k < idx) b.classList.add('done');
      else if (k === idx) { void b.offsetWidth; b.classList.add('active'); }      // reflow restarts the fill animation
    });
    stage.dataset.kind = scenes[idx].dataset.kind;                                  // lets the CSS recolour the footer per slide
    if (where) where.textContent = (idx + 1) + ' / ' + scenes.length;
    body.classList.toggle('paused', !playing);
    if (playBtn) playBtn.textContent = playing ? 'Pause' : 'Play';
  }
  function clear() { if (timer) { clearTimeout(timer); timer = null; } }
  function schedule(ms) {
    clear();
    remaining = ms; startedAt = performance.now();
    timer = setTimeout(function () {
      if (idx < scenes.length - 1) go(idx + 1);
      else if (loop) go(0);
      else { playing = false; clear(); render(); }
    }, ms);
  }
  function go(i) {
    idx = Math.max(0, Math.min(scenes.length - 1, i));
    render();
    if (playing) schedule(seconds(idx) * 1000);
  }
  function pause() { if (!playing) return; playing = false; remaining -= performance.now() - startedAt; clear(); render(); }
  function play() {
    if (playing) return;
    if (idx === scenes.length - 1 && remaining <= 0) { idx = 0; }
    playing = true; render(); schedule(remaining > 0 ? remaining : seconds(idx) * 1000);
  }
  function toggle() { playing ? pause() : play(); }

  document.getElementById('prev')?.addEventListener('click', function () { go(idx - 1); });
  document.getElementById('next')?.addEventListener('click', function () { go(idx + 1); });
  document.getElementById('restart')?.addEventListener('click', function () { playing = true; go(0); });
  playBtn?.addEventListener('click', toggle);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowRight') go(idx + 1);
    else if (e.key === 'ArrowLeft') go(idx - 1);
    else if (e.key === ' ') { toggle(); e.preventDefault(); }
    else if (e.key === 'r' || e.key === 'R') { playing = true; go(0); }
    else if (e.key === 'c' || e.key === 'C') body.classList.toggle('clean');
    else if (e.key === 'f' || e.key === 'F') { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.(); }
  });
  window.addEventListener('resize', fit);
  fit(); render();
  if (playing) schedule(seconds(idx) * 1000);
})();
