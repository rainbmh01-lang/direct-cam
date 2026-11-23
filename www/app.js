(function () {
  const preview = document.getElementById('preview');
  const canvas = document.getElementById('captureCanvas');
  const ctx = canvas.getContext('2d', { alpha: false });
  const permOverlay = document.getElementById('permOverlay');
  const permText = document.getElementById('permText');
  const btnRequestPerm = document.getElementById('btnRequestPerm');
  const btnFacing = document.getElementById('btnFacing');
  const btnMic = document.getElementById('btnMic');
  const btnStream = document.getElementById('btnStream');
  const statusText = document.getElementById('statusText');
  const hostInput = document.getElementById('hostInput');

  let currentFacing = 'environment'; // 'environment' | 'user'
  let micEnabled = true;
  let mediaStream = null;
  let audioCtx = null;
  let scriptNode = null;
  let audioSource = null;

  let ws = null;
  let isStreaming = false;
  let manualStopped = false;
  let frameTimer = null;
  let reconnectTimer = null;

  const FALLBACK_HOSTS = [
    '127.0.0.1:8765',
    '10.137.220.68:8765',
    '10.173.3.68:8765'
  ];

  const savedHost = localStorage.getItem('directcam_host');
  if (savedHost) {
    hostInput.value = savedHost;
  }

  hostInput.addEventListener('change', function () {
    localStorage.setItem('directcam_host', hostInput.value.trim());
  });

  function setStatus(msg) {
    statusText.textContent = msg;
  }

  async function initMedia() {
    setStatus('جار تشغيل الكاميرا...');
    try {
      if (mediaStream) {
        mediaStream.getTracks().forEach(function (t) { t.stop(); });
      }

      const constraints = {
        video: {
          facingMode: { ideal: currentFacing },
          width: { ideal: 640 },
          height: { ideal: 480 },
          frameRate: { ideal: 25 }
        },
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: true
        }
      };

      try {
        mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
      } catch (innerErr) {
        mediaStream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: currentFacing },
          audio: true
        });
      }

      preview.srcObject = mediaStream;
      try {
        await preview.play();
      } catch (playErr) {}

      if (currentFacing === 'user') {
        preview.classList.add('mirror');
        btnFacing.textContent = 'الكاميرا: أمامية';
      } else {
        preview.classList.remove('mirror');
        btnFacing.textContent = 'الكاميرا: خلفية';
      }

      applyMicState();
      setupAudioCapture();
      permOverlay.classList.add('hidden');
      setStatus('الكاميرا والمايك جاهزان');

      if (!isStreaming && !manualStopped) {
        connectWithFallback(0);
      }
    } catch (err) {
      permOverlay.classList.remove('hidden');
      permText.textContent = 'تعذر الوصول للكاميرا أو المايك: ' + (err && err.message ? err.message : 'مرفوض');
      setStatus('بانتظار الإذن');
    }
  }

  function applyMicState() {
    if (mediaStream) {
      mediaStream.getAudioTracks().forEach(function (track) {
        track.enabled = micEnabled;
      });
    }
    if (micEnabled) {
      btnMic.textContent = 'المايك: مفعل';
      btnMic.classList.add('active');
    } else {
      btnMic.textContent = 'المايك: مكتوم';
      btnMic.classList.remove('active');
    }
  }

  function setupAudioCapture() {
    try {
      if (!mediaStream || mediaStream.getAudioTracks().length === 0) return;

      if (scriptNode) {
        try { scriptNode.disconnect(); } catch (e) {}
      }
      if (audioSource) {
        try { audioSource.disconnect(); } catch (e) {}
      }
      if (!audioCtx) {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AudioContextClass({ sampleRate: 16000 });
      }
      if (audioCtx.state === 'suspended') {
        audioCtx.resume();
      }

      audioSource = audioCtx.createMediaStreamSource(mediaStream);
      scriptNode = audioCtx.createScriptProcessor(2048, 1, 1);

      scriptNode.onaudioprocess = function (e) {
        if (!isStreaming || !micEnabled || !ws || ws.readyState !== WebSocket.OPEN) return;
        if (ws.bufferedAmount > 131072) return;

        const input = e.inputBuffer.getChannelData(0);
        const pcm16 = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
          let s = Math.max(-1, Math.min(1, input[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        const packet = new Uint8Array(1 + pcm16.byteLength);
        packet[0] = 0x02; // Audio packet tag
        packet.set(new Uint8Array(pcm16.buffer), 1);
        ws.send(packet.buffer);
      };

      audioSource.connect(scriptNode);
      scriptNode.connect(audioCtx.destination);
    } catch (e) {}
  }

  function dataUrlToPacket(dataUrl) {
    const commaIdx = dataUrl.indexOf(',');
    if (commaIdx === -1) return null;
    const binStr = atob(dataUrl.substring(commaIdx + 1));
    const len = binStr.length;
    const packet = new Uint8Array(1 + len);
    packet[0] = 0x01; // Video JPEG packet tag
    for (let i = 0; i < len; i++) {
      packet[1 + i] = binStr.charCodeAt(i);
    }
    return packet;
  }

  function startVideoLoop() {
    stopVideoLoop();
    frameTimer = setInterval(function () {
      if (!isStreaming || !ws || ws.readyState !== WebSocket.OPEN) return;
      if (ws.bufferedAmount > 262144) return;

      const vw = preview.videoWidth || 640;
      const vh = preview.videoHeight || 480;
      if (preview.readyState < 2) return;

      if (canvas.width !== vw || canvas.height !== vh) {
        canvas.width = vw;
        canvas.height = vh;
      }

      try {
        ctx.drawImage(preview, 0, 0, vw, vh);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.68);
        const packet = dataUrlToPacket(dataUrl);
        if (packet && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(packet.buffer);
        }
      } catch (e) {}
    }, 45);
  }

  function stopVideoLoop() {
    if (frameTimer) {
      clearInterval(frameTimer);
      frameTimer = null;
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (manualStopped) return;
    reconnectTimer = setTimeout(function () {
      if (!isStreaming && !manualStopped) {
        connectWithFallback(0);
      }
    }, 2500);
  }

  function connectWithFallback(index) {
    const manualHost = hostInput.value.trim();
    const candidates = [];
    if (manualHost) candidates.push(manualHost);
    FALLBACK_HOSTS.forEach(function (h) {
      if (candidates.indexOf(h) === -1) candidates.push(h);
    });

    if (index >= candidates.length) {
      setStatus('بانتظار مستقبل الكمبيوتر...');
      scheduleReconnect();
      return;
    }

    const targetHost = candidates[index];
    connectWebSocket(targetHost, function (connected) {
      if (!connected && !isStreaming) {
        connectWithFallback(index + 1);
      }
    });
  }

  function connectWebSocket(host, onResult) {
    try {
      if (ws) {
        ws.onclose = null;
        ws.onerror = null;
        ws.close();
      }
    } catch (e) {}

    let settled = false;
    const url = 'ws://' + host + '/ws';
    setStatus('اتصال: ' + host);

    try {
      ws = new WebSocket(url);
      ws.binaryType = 'arraybuffer';
    } catch (err) {
      if (onResult) onResult(false);
      return;
    }

    const timeout = setTimeout(function () {
      if (!settled && ws && ws.readyState !== WebSocket.OPEN) {
        settled = true;
        try { ws.close(); } catch (e) {}
        if (onResult) onResult(false);
      }
    }, 1800);

    ws.onopen = function () {
      clearTimeout(timeout);
      settled = true;
      isStreaming = true;
      hostInput.value = host;
      localStorage.setItem('directcam_host', host);
      btnStream.textContent = 'إيقاف البث';
      btnStream.classList.add('active');
      setStatus('يبث: ' + host);
      if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
      startVideoLoop();
      if (onResult) onResult(true);
    };

    ws.onclose = function () {
      clearTimeout(timeout);
      stopVideoLoop();
      isStreaming = false;
      btnStream.textContent = 'بدء البث';
      btnStream.classList.remove('active');
      setStatus('انقطع الاتصال');
      if (!settled && onResult) {
        settled = true;
        onResult(false);
      } else {
        scheduleReconnect();
      }
    };

    ws.onerror = function () {
      clearTimeout(timeout);
      if (!settled && onResult) {
        settled = true;
        onResult(false);
      }
    };
  }

  function stopStreaming() {
    manualStopped = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    stopVideoLoop();
    isStreaming = false;
    if (ws) {
      ws.onclose = null;
      try { ws.close(); } catch (e) {}
      ws = null;
    }
    btnStream.textContent = 'بدء البث';
    btnStream.classList.remove('active');
    setStatus('متوقف');
  }

  btnFacing.addEventListener('click', function () {
    currentFacing = currentFacing === 'environment' ? 'user' : 'environment';
    initMedia();
  });

  btnMic.addEventListener('click', function () {
    micEnabled = !micEnabled;
    applyMicState();
  });

  btnStream.addEventListener('click', function () {
    if (isStreaming) {
      stopStreaming();
    } else {
      manualStopped = false;
      connectWithFallback(0);
    }
  });

  btnRequestPerm.addEventListener('click', function () {
    initMedia();
  });

  window.addEventListener('load', function () {
    initMedia();
  });
})();
