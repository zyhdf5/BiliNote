/* settings page tabs */
const tabsRoot = document.getElementById('settings-tabs');
if (tabsRoot) {
  const tabs = Array.from(document.querySelectorAll('.tab'));
  const panels = Array.from(tabsRoot.querySelectorAll('.tab-panel'));

  const activate = (id) => {
    for (const tab of tabs) {
      const on = tab.dataset.tab === id;
      tab.classList.toggle('active', on);
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
    }
    for (const panel of panels) {
      panel.classList.toggle('active', panel.id === id);
    }
  };

  for (const tab of tabs) {
    tab.addEventListener('click', () => {
      activate(tab.dataset.tab);
      history.replaceState(null, '', '#' + tab.dataset.tab);
    });
  }

  const initial = location.hash.slice(1);
  activate(panels.some(p => p.id === initial) ? initial : tabs[0].dataset.tab);
  tabsRoot.classList.add('tabs-on');

  // keep the current tab across the save POST reload
  const form = document.getElementById('settings-form');
  form.addEventListener('submit', () => {
    form.action = '/settings' + location.hash;
  });
}

/* system test buttons */
for (const btn of document.querySelectorAll('[data-test]')) {
  btn.addEventListener('click', async () => {
    const out = document.getElementById('test-result');
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    out.textContent = '检测中…';
    btn.disabled = true;
    try {
      const r = await fetch(btn.dataset.test, {
        method: 'POST',
        headers: {'X-CSRF-Token': csrf}
      });
      const text = await r.text();
      out.textContent = (r.ok ? '成功：' : '失败：') + text.slice(0, 500);
    } catch (e) {
      out.textContent = '失败：' + e;
    } finally {
      btn.disabled = false;
    }
  });
}

/* Whisper model: trigger async download+load, then poll status */
const asrBtn = document.querySelector('[data-asr-load]');
if (asrBtn) {
  const fmtMb = (n) => Math.round((n || 0) / 1048576);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const pollAsrStatus = async (out) => {
    for (;;) {
      const r = await fetch('/api/v1/system/asr-status');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const s = await r.json();
      if (s.state === 'loaded') {
        out.textContent = '成功：Whisper 模型已加载（' + (s.model || '') + '）';
        return;
      }
      if (s.state === 'error') {
        out.textContent = '失败：' + (s.error || '模型加载失败').slice(0, 500);
        return;
      }
      if (s.state === 'downloading') {
        const pct = s.total_bytes ? Math.round((s.downloaded_bytes * 100) / s.total_bytes) : 0;
        out.textContent = '下载模型中：' + s.file + ' ' + fmtMb(s.downloaded_bytes) + '/' + fmtMb(s.total_bytes) + ' MB（' + pct + '%）';
      } else if (s.state === 'loading') {
        out.textContent = '加载模型到 GPU 中…';
      } else {
        out.textContent = '准备中…';
      }
      await sleep(2000);
    }
  };

  asrBtn.addEventListener('click', async () => {
    const out = document.getElementById('test-result');
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    asrBtn.disabled = true;
    try {
      const r = await fetch('/api/v1/system/test-asr', {
        method: 'POST',
        headers: {'X-CSRF-Token': csrf}
      });
      if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + (await r.text()).slice(0, 300));
      await pollAsrStatus(out);
    } catch (e) {
      out.textContent = '失败：' + e;
    } finally {
      asrBtn.disabled = false;
    }
  });
}
