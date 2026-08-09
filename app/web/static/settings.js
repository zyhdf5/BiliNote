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
