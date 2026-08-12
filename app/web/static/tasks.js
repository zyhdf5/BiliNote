// 任务列表页：轮询 JSON API，只重建列表区域，不整页刷新。
(function () {
  'use strict';

  var panel = document.getElementById('tasks-panel');
  if (!panel) return;

  function row(task) {
    var a = document.createElement('a');
    a.className = 'task-row';
    a.href = '/tasks/' + task.id;

    var title = document.createElement('span');
    title.className = 'task-title';
    title.textContent = task.title || task.url;

    var status = document.createElement('span');
    status.className = 'status ' + task.status;
    status.textContent = task.status;

    var stage = document.createElement('span');
    stage.textContent = task.stage || '';

    var progress = document.createElement('span');
    progress.className = 'progress';
    progress.textContent = String(task.progress) + '%';

    a.appendChild(title);
    a.appendChild(status);
    a.appendChild(stage);
    a.appendChild(progress);
    return a;
  }

  function render(tasks) {
    var list = panel.querySelector('.task-list');
    if (!tasks.length) return; // 空态由服务端首屏渲染；有任务后才有列表可更新
    if (!list) {
      list = document.createElement('div');
      list.className = 'task-list';
      panel.textContent = '';
      panel.appendChild(list);
    }
    var fragment = document.createDocumentFragment();
    tasks.forEach(function (task) { fragment.appendChild(row(task)); });
    list.replaceChildren(fragment);
  }

  function poll() {
    fetch('/api/v1/tasks?limit=200', { headers: { 'Accept': 'application/json' } })
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function (data) { render(data.items || []); setTimeout(poll, 5000); })
      .catch(function () { setTimeout(poll, 8000); }); // 网络抖动时退避重试
  }

  setTimeout(poll, 5000);
})();
