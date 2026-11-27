/* ============================================================================
 * static/js/progress.js  —— D同学负责
 * ----------------------------------------------------------------------------
 * 上传页交互逻辑：
 *   1) 拖拽 / 点击选择文件
 *   2) 提交表单 -> POST /upload -> 拿到 task_id
 *   3) 通过 Socket.IO 监听 'upload_progress' 事件，实时刷新进度条
 *   4) 兜底：若 Socket.IO 不可用（eventlet 未装好等），自动降级为
 *      轮询 GET /upload/progress/<task_id>
 *   5) 完成 / 失败后给出友好提示与跳转入口
 *
 * 事件协议（与 routes/upload.py 严格对应）：
 *   event : 'upload_progress'
 *   data  : { task_id, progress:0-100, message, status:'running'|'done'|'error' }
 * ==========================================================================*/

function initUploadPage(cfg) {
  var uploadUrl   = cfg.uploadUrl   || '/upload';
  var progressUrl = cfg.progressUrl || '/upload/progress/';

  var form       = document.getElementById('uploadForm');
  var dropZone   = document.getElementById('dropZone');
  var dropHint   = document.getElementById('dropHint');
  var fileInput  = document.getElementById('fileInput');
  var submitBtn  = document.getElementById('submitBtn');
  var wrap       = document.getElementById('progressWrap');
  var bar        = document.getElementById('progressBar');
  var pct        = document.getElementById('progressPct');
  var label      = document.getElementById('progressLabel');
  var resultBox  = document.getElementById('resultBox');

  var currentTaskId = null;
  var pollingTimer  = null;
  var finished      = false;

  /* ------------------------------------------------ 文件选择 */
  dropZone.addEventListener('click', function () { fileInput.click(); });

  dropZone.addEventListener('dragover', function (e) {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });
  dropZone.addEventListener('dragleave', function () {
    dropZone.classList.remove('dragover');
  });
  dropZone.addEventListener('drop', function (e) {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      showPickedFile(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener('change', function () {
    if (fileInput.files.length) showPickedFile(fileInput.files[0]);
  });

  function showPickedFile(f) {
    var sizeMB = (f.size / 1024 / 1024).toFixed(2);
    dropHint.innerHTML =
      '<div style="font-size:2rem;">✅</div>' +
      '<div class="mt-2 fw-bold">' + escapeHtml(f.name) + '</div>' +
      '<div class="small text-muted mt-1">' + sizeMB + ' MB　点击可重新选择</div>';
    dropZone.classList.add('has-file');
  }

  /* ------------------------------------------------ 进度渲染 */
  function setProgress(progress, message, status) {
    progress = Math.max(0, Math.min(100, parseInt(progress, 10) || 0));
    wrap.style.display = 'block';
    bar.style.width = progress + '%';
    bar.textContent = progress + '%';
    bar.setAttribute('aria-valuenow', progress);
    pct.textContent = progress + '%';
    if (message) label.textContent = message;

    if (status === 'done') {
      bar.classList.remove('progress-bar-animated', 'bg-danger');
      bar.classList.add('bg-success');
      finish(true, message);
    } else if (status === 'error') {
      bar.classList.remove('progress-bar-animated');
      bar.classList.add('bg-danger');
      finish(false, message);
    }
  }

  function finish(ok, message) {
    if (finished) return;
    finished = true;
    stopPolling();
    submitBtn.disabled = false;
    submitBtn.textContent = '再上传一篇';

    var paper = fileInput.files && fileInput.files.length ? fileInput.files[0].name : '';
    if (ok) {
      resultBox.innerHTML =
        '<div class="alert alert-success mb-2">' + escapeHtml(message || '处理完成') + '</div>' +
        '<a class="btn btn-outline-success btn-sm me-1" href="/paper/' +
        encodeURIComponent(paper) + '">查看术语列表 →</a> ' +
        '<a class="btn btn-outline-danger btn-sm me-1" href="/paper/' +
        encodeURIComponent(paper) + '/graph">词汇关系图谱 →</a> ' +
        '<a class="btn btn-outline-secondary btn-sm" href="/">返回首页</a>';
    } else {
      resultBox.innerHTML =
        '<div class="alert alert-danger mb-2">' + escapeHtml(message || '处理失败') + '</div>' +
        '<div class="small text-muted">请检查文件是否为扫描版 PDF、是否为空，或稍后重试。</div>';
    }
  }

  /* ------------------------------------------------ Socket.IO */
  var socket = null;
  try {
    if (typeof io === 'function') {
      socket = io();                       // 同源连接
      socket.on('connect', function () {
        console.log('[progress] Socket.IO 已连接');
        stopPolling();                     // 连上了就不需要轮询
      });
      socket.on('disconnect', function () {
        console.log('[progress] Socket.IO 断开，启用轮询兜底');
        if (currentTaskId && !finished) startPolling();
      });
      socket.on('upload_progress', function (d) {
        // 多用户同时上传时，只处理属于自己的任务
        if (!d || (currentTaskId && d.task_id !== currentTaskId)) return;
        setProgress(d.progress, d.message, d.status);
      });
    }
  } catch (e) {
    console.warn('[progress] Socket.IO 初始化失败，将使用轮询：', e);
  }

  /* ------------------------------------------------ 轮询兜底 */
  function startPolling() {
    if (pollingTimer) return;
    pollingTimer = setInterval(function () {
      if (!currentTaskId || finished) { stopPolling(); return; }
      fetch(progressUrl + encodeURIComponent(currentTaskId))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) return;
          setProgress(d.progress, d.message, d.status);
        })
        .catch(function () { /* 忽略单次失败 */ });
    }, 1000);
  }
  function stopPolling() {
    if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null; }
  }

  /* ------------------------------------------------ 提交 */
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (!fileInput.files || !fileInput.files.length) {
      alert('请先选择要上传的文献文件');
      return;
    }
    var f = fileInput.files[0];
    var ext = f.name.split('.').pop().toLowerCase();
    if (ext !== 'txt' && ext !== 'pdf') {
      alert('只支持 TXT 或 PDF 文件');
      return;
    }
    if (f.size > 50 * 1024 * 1024) {
      alert('文件超过 50MB，请拆分后再上传');
      return;
    }

    finished = false;
    submitBtn.disabled = true;
    submitBtn.textContent = '上传中…';
    resultBox.innerHTML = '';
    setProgress(0, '正在上传文件…', 'running');

    var fd = new FormData();
    fd.append('file', f);
    fd.append('category', document.getElementById('category').value);

    fetch(uploadUrl, { method: 'POST', body: fd })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          finish(false, res.d && res.d.error ? res.d.error : '上传失败');
          return;
        }
        currentTaskId = res.d.task_id;
        setProgress(5, '文件已接收，正在后台解析…', 'running');
        // 若 3 秒内没收到 Socket.IO 推送，就启动轮询兜底
        setTimeout(function () {
          if (!finished && (!socket || !socket.connected)) startPolling();
        }, 3000);
      })
      .catch(function (err) {
        finish(false, '网络异常：' + err);
      });
  });

  /* ------------------------------------------------ 工具 */
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
}
