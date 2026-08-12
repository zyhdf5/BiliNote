// 任务详情页：轮询 JSON API，只更新变化的 DOM，不整页刷新。
// 任务进入终态（succeeded/failed/canceled）时刷新一次，由服务端渲染总结/错误。
(function () {
  'use strict';

  var match = location.pathname.match(/^\/tasks\/([0-9a-f]{32})\/?$/);
  if (!match) return;
  var taskId = match[1];

  var statusEl = document.getElementById('task-status');
  var stageEl = document.getElementById('task-stage');
  var progressEl = document.getElementById('task-progress');
  var barEl = document.getElementById('task-bar-fill');
  var barWrapEl = document.getElementById('task-bar');
  var sourceEl = document.getElementById('task-source');
  var logEl = document.getElementById('task-log-tail');
  var titleEl = document.getElementById('task-title');

  var TERMINAL = ['succeeded', 'failed', 'canceled'];
  var STATUS_CLASSES = ['queued', 'running', 'succeeded', 'failed', 'canceled'];

  function setText(el, value) {
    if (el && el.textContent !== value) el.textContent = value;
  }

  function apply(task) {
    if (TERMINAL.indexOf(task.status) !== -1) {
      // 终态：整页刷新一次，渲染总结 Markdown / 错误面板（服务端渲染 + sanitize）。
      location.reload();
      return;
    }
    if (statusEl) {
      setText(statusEl, task.status);
      STATUS_CLASSES.forEach(function (c) { statusEl.classList.remove(c); });
      statusEl.classList.add(task.status);
    }
    setText(stageEl, task.stage || '');
    var pct = String(task.progress) + '%';
    setText(progressEl, pct);
    if (barEl) barEl.style.width = pct;
    if (barWrapEl) barWrapEl.setAttribute('aria-valuenow', String(task.progress));
    setText(sourceEl, task.transcript_source || '-');
    if (task.title) setText(titleEl, task.title);
    if (logEl && typeof task.log_tail === 'string') setText(logEl, task.log_tail);
    schedule(3000);
  }

  function schedule(delay) {
    setTimeout(poll, delay);
  }

  function poll() {
    fetch('/api/v1/tasks/' + taskId, { headers: { 'Accept': 'application/json' } })
      .then(function (resp) {
        if (resp.status === 404) {
          // 任务已结束并被清理（如用户取消后直接删除），回到列表页。
          location.href = '/tasks';
          return null;
        }
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function (task) { if (task) apply(task); })
      .catch(function () { schedule(5000); }); // 网络抖动时退避重试
  }

  schedule(3000);
})();
