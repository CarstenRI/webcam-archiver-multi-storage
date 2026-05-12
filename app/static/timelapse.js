/* Timelapse-Player (v0.10.0)
 *
 * Aufgaben:
 *  1) Vorschau-Slideshow: Holt eine Frame-Liste vom Backend, swappt das <img>
 *     im FPS-Takt, Scrub-Bar, Prev/Next. Lazy-Loading: zieht weitere Pages
 *     nach, wenn die aktuelle Seite ueber 80% durchgespielt ist.
 *  2) Render-Form: POST nach /api/cams/{id}/timelapse/render, danach 2s-Polling
 *     gegen /api/timelapse/jobs/{job_id}, UI-Update der Job-Tabelle.
 *  3) Job-Loesch-Bestaetigung mit Fetch-Patch fuer SPA-Feeling.
 *
 * Konsumiert window.TL_CTX = {camId, preferredTargetId, ffmpegAvailable}.
 * Wird nur ausgefuehrt, wenn das Timelapse-Tab im DOM ist (sonst kein-op).
 */
(function() {
  if (!window.TL_CTX) return;
  var ctx = window.TL_CTX;

  // --- DOM-Refs (alle optional — wenn der Tab nicht gerendert wird, sind die null) ---
  var elSource = document.getElementById('tl-source');
  var elFrom = document.getElementById('tl-from');
  var elTo = document.getElementById('tl-to');
  var elTimeStart = document.getElementById('tl-time-start');
  var elTimeEnd = document.getElementById('tl-time-end');
  var elBestOfDay = document.getElementById('tl-best-of-day');
  var elLoadBtn = document.getElementById('tl-load-btn');
  var elLoadInfo = document.getElementById('tl-load-info');
  var elImage = document.getElementById('tl-image');
  var elEmpty = document.getElementById('tl-stage-empty');
  var elPrev = document.getElementById('tl-prev');
  var elNext = document.getElementById('tl-next');
  var elPlay = document.getElementById('tl-play');
  var elTs = document.getElementById('tl-ts');
  var elCounter = document.getElementById('tl-counter');
  var elFps = document.getElementById('tl-fps');
  var elFpsVal = document.getElementById('tl-fps-val');
  var elScrub = document.getElementById('tl-scrub');
  var elSourceInfo = document.getElementById('tl-source-info');
  var elRenderBtn = document.getElementById('tl-render-btn');
  var elRenderFps = document.getElementById('tl-render-fps');
  var elResolution = document.getElementById('tl-resolution');
  var elRenderInfo = document.getElementById('tl-render-info');
  var elJobsBody = document.getElementById('tl-jobs-body');

  if (!elLoadBtn) return; // Cam hat kein Local-Target — Tab zeigt Info-Card

  // --- State ---
  var state = {
    frames: [],
    page: 1,
    pageSize: 1000,
    hasMore: false,
    fetching: false,
    idx: 0,
    playing: false,
    timer: null,
    fps: parseInt(elFps.value, 10) || 15
  };

  function fmtTs(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var pad = function(n){ return n < 10 ? '0' + n : '' + n; };
    return d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate())
      + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  function fmtBytes(b) {
    if (!b) return '—';
    if (b < 1024) return b + ' B';
    if (b < 1024*1024) return (b/1024).toFixed(1) + ' KB';
    if (b < 1024*1024*1024) return (b/1024/1024).toFixed(1) + ' MB';
    return (b/1024/1024/1024).toFixed(2) + ' GB';
  }

  function updateSourceInfo() {
    if (!elSource || !elSourceInfo) return;
    var opt = elSource.options[elSource.selectedIndex];
    if (!opt) { elSourceInfo.textContent = ''; return; }
    var c = opt.dataset.count || '0';
    var first = opt.dataset.first;
    var last = opt.dataset.last;
    if (parseInt(c, 10) === 0) {
      elSourceInfo.textContent = 'Noch keine Bilder auf diesem Target.';
      return;
    }
    elSourceInfo.textContent = c + ' Bilder · ' + fmtTs(first) + ' – ' + fmtTs(last);
    // Defaults fuer Date-Range setzen, falls leer
    if (!elFrom.value && first) elFrom.value = first.slice(0, 16);
    if (!elTo.value && last) elTo.value = last.slice(0, 16);
  }
  if (elSource) elSource.addEventListener('change', updateSourceInfo);
  updateSourceInfo();

  // FPS-Slider Live-Anzeige
  if (elFps && elFpsVal) {
    elFps.addEventListener('input', function() {
      state.fps = parseInt(elFps.value, 10) || 15;
      elFpsVal.textContent = state.fps;
      if (state.playing) {
        stopPlayback();
        startPlayback();
      }
    });
  }

  function buildFiltersQS() {
    var p = new URLSearchParams();
    var src = elSource.value;
    if (src) p.set('source_target_id', src);
    if (elFrom.value) p.set('from', elFrom.value);
    if (elTo.value) p.set('to', elTo.value);
    // Wochentage als 7-Bit-String einsammeln
    var bits = '';
    var wdInputs = document.querySelectorAll('#tl-weekdays input[type=checkbox]');
    wdInputs.forEach(function(cb) { bits += cb.checked ? '1' : '0'; });
    if (bits && bits !== '1111111') p.set('weekdays', bits);
    if (elTimeStart.value) p.set('time_start', elTimeStart.value);
    if (elTimeEnd.value) p.set('time_end', elTimeEnd.value);
    return p;
  }

  function loadPage(page, append) {
    if (state.fetching) return;
    state.fetching = true;
    if (elLoadInfo) elLoadInfo.textContent = (page === 1 ? 'lade…' : 'lade Page ' + page + ' …');
    var p = buildFiltersQS();
    p.set('page', page);
    p.set('page_size', state.pageSize);
    return fetch('/api/cams/' + ctx.camId + '/timelapse/frames?' + p.toString(), {
      credentials: 'same-origin'
    }).then(function(r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function(j) {
      if (j.error === 'no_local_target') {
        if (elLoadInfo) elLoadInfo.textContent = 'Cam hat kein Local-Target.';
        return;
      }
      state.hasMore = j.has_more;
      state.page = j.page || 1;
      if (append) {
        state.frames = state.frames.concat(j.frames);
      } else {
        state.frames = j.frames || [];
        state.idx = 0;
      }
      if (elLoadInfo) {
        elLoadInfo.textContent = state.frames.length + ' / ' + (j.total || state.frames.length)
          + ' Frames geladen' + (state.hasMore ? ' (weitere folgen)' : '');
      }
      if (state.frames.length === 0) {
        if (elEmpty) {
          elEmpty.style.display = 'flex';
          elEmpty.textContent = 'Keine Frames im gewählten Zeitraum.';
        }
        if (elImage) elImage.removeAttribute('src');
      } else {
        if (elEmpty) elEmpty.style.display = 'none';
        elScrub.max = state.frames.length - 1;
        if (!append) showFrame(0);
        elCounter.textContent = state.frames.length + (j.total > state.frames.length ? ' / ' + j.total : '');
      }
    }).catch(function(e) {
      if (elLoadInfo) elLoadInfo.textContent = 'Fehler: ' + e.message;
    }).then(function() {
      state.fetching = false;
    });
  }

  function showFrame(i) {
    if (!state.frames.length) return;
    i = Math.max(0, Math.min(state.frames.length - 1, i));
    state.idx = i;
    var f = state.frames[i];
    if (elImage) {
      elImage.src = f.url;
      elImage.style.display = 'block';
    }
    if (elTs) elTs.textContent = fmtTs(f.ts);
    if (elScrub) elScrub.value = i;
    if (elCounter) elCounter.textContent = (i+1) + ' / ' + state.frames.length;
    // Lazy-Load: wenn 80% durch und has_more, naechste Seite holen
    if (state.hasMore && !state.fetching && i / state.frames.length > 0.8) {
      loadPage(state.page + 1, true);
    }
  }

  function startPlayback() {
    if (state.playing || !state.frames.length) return;
    state.playing = true;
    if (elPlay) elPlay.textContent = '⏸';
    var interval = Math.max(20, Math.floor(1000 / state.fps));
    state.timer = setInterval(function() {
      var nxt = state.idx + 1;
      if (nxt >= state.frames.length) {
        if (state.hasMore) {
          // warten — naechste Page kommt automatisch via Lazy-Load oder Stop
          return;
        }
        nxt = 0; // Loop
      }
      showFrame(nxt);
    }, interval);
  }

  function stopPlayback() {
    state.playing = false;
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    if (elPlay) elPlay.textContent = '▶';
  }

  if (elLoadBtn) elLoadBtn.addEventListener('click', function() {
    stopPlayback();
    loadPage(1, false);
  });
  if (elPrev) elPrev.addEventListener('click', function() { stopPlayback(); showFrame(state.idx - 1); });
  if (elNext) elNext.addEventListener('click', function() { stopPlayback(); showFrame(state.idx + 1); });
  if (elPlay) elPlay.addEventListener('click', function() {
    if (state.playing) stopPlayback(); else startPlayback();
  });
  if (elScrub) elScrub.addEventListener('input', function() {
    stopPlayback();
    showFrame(parseInt(elScrub.value, 10) || 0);
  });

  // Tastatur-Shortcuts (nur im Timelapse-Tab)
  document.addEventListener('keydown', function(e) {
    var panel = document.querySelector('.tab-panel.active[data-tab-panel="timelapse"]');
    if (!panel) return;
    if (['INPUT','TEXTAREA','SELECT'].indexOf(e.target.tagName) !== -1) return;
    if (e.key === ' ') { e.preventDefault(); if (state.playing) stopPlayback(); else startPlayback(); }
    else if (e.key === 'ArrowLeft') { stopPlayback(); showFrame(state.idx - 1); }
    else if (e.key === 'ArrowRight') { stopPlayback(); showFrame(state.idx + 1); }
  });

  // --- Render-Form ---
  if (elRenderBtn && !elRenderBtn.disabled) {
    elRenderBtn.addEventListener('click', function() {
      if (!state.frames.length && !confirm('Es sind noch keine Frames geladen. Trotzdem rendern (mit aktuellen Filter-Werten)?')) {
        return;
      }
      var qs = buildFiltersQS();
      var body = {
        source_target_id: parseInt(elSource.value, 10) || null,
        from: elFrom.value,
        to: elTo.value,
        fps: parseInt(elRenderFps.value, 10) || 25,
        resolution: elResolution.value,
        time_start: elTimeStart.value || null,
        time_end: elTimeEnd.value || null,
        weekdays: (function() {
          var bits = '';
          document.querySelectorAll('#tl-weekdays input[type=checkbox]').forEach(function(cb){
            bits += cb.checked ? '1' : '0';
          });
          return bits === '1111111' ? null : bits;
        })(),
        best_of_day: !!elBestOfDay.checked
      };
      if (!body.from || !body.to) {
        elRenderInfo.textContent = '✗ Von/Bis bitte ausfüllen.';
        return;
      }
      elRenderBtn.disabled = true;
      elRenderInfo.textContent = 'starte Render…';
      fetch('/api/cams/' + ctx.camId + '/timelapse/render', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function(r) {
        return r.json().then(function(j){ return {ok: r.ok, j: j}; });
      }).then(function(res) {
        elRenderBtn.disabled = false;
        if (!res.ok) {
          elRenderInfo.textContent = '✗ ' + (res.j.detail || 'Fehler');
          return;
        }
        elRenderInfo.textContent = '✓ Job ' + res.j.job_id + ' eingereiht. Polling läuft…';
        startJobPoll(res.j.job_id);
      }).catch(function(e) {
        elRenderBtn.disabled = false;
        elRenderInfo.textContent = '✗ ' + e.message;
      });
    });
  }

  function startJobPoll(jobId) {
    var interval = setInterval(function() {
      fetch('/api/timelapse/jobs/' + jobId, { credentials: 'same-origin' })
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(j) {
          if (!j) { clearInterval(interval); return; }
          updateJobRow(j);
          if (j.status === 'done' || j.status === 'error') {
            clearInterval(interval);
            // Nach Erfolg: page reload, damit der Job in der Tabelle erscheint
            if (j.status === 'done') {
              setTimeout(function() { location.reload(); }, 600);
            }
          }
        }).catch(function() { /* still trying */ });
    }, 2000);
  }

  function updateJobRow(j) {
    if (elRenderInfo) {
      if (j.status === 'running') {
        elRenderInfo.textContent = '⟳ rendert… ' + (j.progress_pct || 0) + '%' +
          (j.frame_count ? ' (' + j.frame_count + ' Frames)' : '');
      } else if (j.status === 'pending') {
        elRenderInfo.textContent = '⏳ wartet auf Worker…';
      } else if (j.status === 'done') {
        elRenderInfo.textContent = '✓ fertig (' + fmtBytes(j.bytes) + ' in ' +
          (j.duration_s ? j.duration_s.toFixed(0) : '?') + 's) — Tabelle aktualisiert sich…';
      } else if (j.status === 'error') {
        elRenderInfo.textContent = '✗ Render-Fehler: ' + (j.error_message || 'unbekannt');
      }
    }
    // Inline-Update einer existierenden Job-Row
    var row = document.querySelector('tr[data-job-id="' + j.job_id + '"]');
    if (row) {
      row.dataset.jobStatus = j.status;
      var statusCell = row.querySelector('td:first-child');
      if (statusCell) {
        if (j.status === 'running') {
          statusCell.innerHTML = '<span class="pill status-pending">⟳ rendert ' + (j.progress_pct || 0) + '%</span>';
        } else if (j.status === 'pending') {
          statusCell.innerHTML = '<span class="pill status-pending">⏳ wartet</span>';
        }
      }
    }
  }

  // Bereits laufende Jobs beim Page-Load weiterpollen (resume nach Reload)
  document.querySelectorAll('tr[data-job-id]').forEach(function(row) {
    var st = row.dataset.jobStatus;
    if (st === 'pending' || st === 'running') {
      startJobPoll(parseInt(row.dataset.jobId, 10));
    }
  });

  // Cam-Test-Button (nur in Bearbeiten-Tab) -- reuse from existing app.js if loaded
  window.testCam = window.testCam || function(camId) {
    fetch('/cams/' + camId + '/test', { method: 'POST', credentials: 'same-origin' })
      .then(function(r){ return r.json(); })
      .then(function(j) {
        if (j.ok) alert('URL OK · ' + j.bytes + ' Bytes geladen.');
        else alert('Fehler: ' + (j.error || 'unbekannt'));
      });
  };
})();

window.tlDeleteJob = function(ev, jobId) {
  ev.preventDefault();
  if (!confirm('Diesen Render-Eintrag samt MP4 wirklich löschen?')) return false;
  fetch('/api/timelapse/jobs/' + jobId + '/delete', {
    method: 'POST', credentials: 'same-origin'
  }).then(function(r){ return r.json(); })
    .then(function() { location.reload(); })
    .catch(function(e) { alert('Fehler: ' + e.message); });
  return false;
};
