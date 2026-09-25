
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const socket = io({ reconnection: true, reconnectionAttempts: Infinity, reconnectionDelay: 500, timeout: 10000 });
  const synth = window.speechSynthesis;
  let recognition = null;
  let recognitionActive = false;
  let recognitionStarting = false;
  let shouldListen = true;
  let restartTimer = null;
  let restartAttempts = 0;
  let lastSentText = '';
  let lastSentAt = 0;
  let isJarvisSpeaking = false;
  let requestCounter = 0;
  let speechBuffer = '';
  let speechFlushTimer = null;
  const pendingRequests = new Map();
  const commandHistory = [];
  const startedAt = Date.now();

  function setStatus(label, detail, speaking = false) {
    $('statusLabel').textContent = label;
    $('statusDetail').textContent = detail || '';
    $('voiceState').textContent = speaking ? 'JARVIS AUDIO OUTPUT' : 'VOICE CHANNEL READY';
    $('footerStatus').textContent = speaking ? 'â— JARVIS RESPONDING' : 'â— LISTENING 24/7';
    $('core').classList.toggle('speaking', speaking);
  }

  function addNotice(text) {
    const list = $('noticeList');
    const item = document.createElement('li');
    item.innerHTML = '<span></span><span></span>';
    item.lastElementChild.textContent = text;
    list.prepend(item);
    while (list.children.length > 3) list.lastElementChild.remove();
  }

  function addCommand(text) {
    if (!text) return;
    const now = new Date();
    const time = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    commandHistory.unshift({ time, text });
    commandHistory.splice(5);
    $('commandCount').textContent = String(commandHistory.length);
    $('historyList').innerHTML = commandHistory.map((item) => `<li><span class="history-time">${item.time}</span><span>${escapeHtml(item.text)}</span></li>`).join('');
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
  }

  function updateClock() {
    $('clock').textContent = new Date().toLocaleString([], { weekday: 'short', month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    const h = String(Math.floor(elapsed / 3600)).padStart(2, '0');
    const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    $('uptime').textContent = `UPTIME: ${h}:${m}:${s}`;
  }

  async function updateBattery() {
    try {
      if (!navigator.getBattery) return;
      const battery = await navigator.getBattery();
      const update = () => { $('battery').textContent = `BATTERY ${Math.round(battery.level * 100)}%${battery.charging ? ' â€¢ CHARGING' : ''}`; };
      update(); battery.addEventListener('levelchange', update); battery.addEventListener('chargingchange', update);
    } catch (_) { $('battery').textContent = 'BATTERY N/A'; }
  }

  function detectLanguageHint(text) {
    if (/[\u0600-\u06FF]/.test(text)) return 'urdu_script';
    if (/[\u0900-\u097F]/.test(text)) return 'hindi_script';
    if (/\b(?:mujhe|muje|aap|apko|tumhe|meri|mere|yaad|yad|bhool|bhul|kya|kia|hai|nahi|nhi|karo|krdo|acha|accha)\b/i.test(text)) return 'roman_urdu_or_hinglish';
    return 'english_or_mixed';
  }

  function sendQuestion(text, languageHint) {
    const requestId = `${Date.now()}-${++requestCounter}`;
    const timeout = setTimeout(() => {
      if (!pendingRequests.has(requestId)) return;
      pendingRequests.delete(requestId);
      $('taskState').textContent = 'RETRY READY';
      $('taskTitle').textContent = 'No response received';
      $('taskDetail').textContent = 'The command channel is still listening.';
      addNotice('No response arrived for one command; voice listening remains active.');
      setStatus('LISTENING', 'No response received. Ready for your next command.');
    }, 40000);
    pendingRequests.set(requestId, timeout);
    socket.emit('user_speech', { text, request_id: requestId, language_hint: languageHint || detectLanguageHint(text) });
  }

  function acknowledgeRequest(requestId) {
    if (requestId && pendingRequests.has(requestId)) {
      clearTimeout(pendingRequests.get(requestId));
      pendingRequests.delete(requestId);
      return;
    }
    if (!requestId && pendingRequests.size) {
      const first = pendingRequests.keys().next().value;
      clearTimeout(pendingRequests.get(first));
      pendingRequests.delete(first);
    }
  }

  function flushSpeechBuffer() {
    clearTimeout(speechFlushTimer);
    speechFlushTimer = null;
    const normalized = speechBuffer.trim().replace(/\s+/g, ' ');
    speechBuffer = '';
    if (!normalized) return;
    const now = Date.now();
    if (normalized === lastSentText && now - lastSentAt <= 1500) return;
    lastSentText = normalized;
    lastSentAt = now;
    addCommand(normalized);
    $('taskTitle').textContent = 'Processing command...';
    $('taskDetail').textContent = normalized;
    setStatus('PROCESSING', 'Understanding your request...');
    sendQuestion(normalized, detectLanguageHint(normalized));
  }

  function queueTranscript(text) {
    const clean = String(text || '').trim();
    if (!clean) return;
    speechBuffer = [speechBuffer, clean].filter(Boolean).join(' ').replace(/\s+/g, ' ');
    clearTimeout(speechFlushTimer);
    speechFlushTimer = setTimeout(flushSpeechBuffer, 850);
  }

  function setupSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) { setStatus('BROWSER AUDIO LIMITED', 'Use Chrome or Edge for live voice.'); return; }
    if (recognitionActive || recognitionStarting) return;
    clearTimeout(restartTimer);
    recognitionStarting = true;
    shouldListen = true;
    const instance = new SpeechRecognition();
    recognition = instance;
    instance.continuous = true;
    instance.interimResults = true;
    instance.lang = 'en-US';
    instance.onstart = () => {
      if (recognition !== instance) return;
      recognitionActive = true;
      recognitionStarting = false;
      restartAttempts = 0;
      $('micButton').classList.add('listening');
      $('voiceModule').textContent = 'LISTENING';
      if (!isJarvisSpeaking) setStatus('LISTENING', 'Ready for your command...');
    };
    instance.onresult = (event) => {
      if (recognition !== instance) return;
      let interim = '';
      let finalTranscript = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) finalTranscript += event.results[i][0].transcript;
        else interim += event.results[i][0].transcript;
      }
      const activeText = (finalTranscript || interim).trim();
      if (activeText) {
        $('userLive').textContent = `YOU: ${activeText}`;
        if (isJarvisSpeaking) { synth.cancel(); isJarvisSpeaking = false; setStatus('LISTENING', 'Interrupted - listening again.'); }
      }
      if (finalTranscript.trim()) queueTranscript(finalTranscript);
    };
    instance.onerror = (event) => {
      if (recognition !== instance) return;
      const permissionError = event.error === 'not-allowed' || event.error === 'service-not-allowed';
      recognitionActive = false;
      recognitionStarting = false;
      recognition = null;
      $('micButton').classList.remove('listening');
      if (permissionError) {
        shouldListen = false;
        setStatus('MICROPHONE PERMISSION NEEDED', 'Allow microphone access, then tap the core once.');
        addNotice('Microphone permission is required for hands-free voice.');
        return;
      }
      if (shouldListen) scheduleRecognitionRestart();
    };
    instance.onend = () => {
      if (recognition !== instance) return;
      flushSpeechBuffer();
      recognitionActive = false;
      recognitionStarting = false;
      recognition = null;
      $('micButton').classList.remove('listening');
      if (shouldListen) scheduleRecognitionRestart();
    };
    try {
      instance.start();
    } catch (_) {
      if (recognition === instance) { recognition = null; recognitionStarting = false; recognitionActive = false; }
      if (shouldListen) scheduleRecognitionRestart();
    }
  }

  function scheduleRecognitionRestart() {
    clearTimeout(restartTimer);
    restartAttempts = Math.min(restartAttempts + 1, 6);
    const delay = Math.min(500 * Math.pow(2, restartAttempts - 1), 5000);
    restartTimer = setTimeout(() => {
      if (shouldListen && !recognitionActive && !recognitionStarting) setupSpeechRecognition();
    }, delay);
  }
  function speakReply(text) {
    if (!text || !synth) return;
    synth.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.04; utterance.pitch = 1;
    const voices = synth.getVoices();
    const hasUrdu = /[\u0600-\u06FF]/.test(text);
    const hasHindi = /[\u0900-\u097F]/.test(text);
    if (hasUrdu) {
      utterance.lang = 'ur-PK';
      const voice = voices.find(v => /^(ur|ar)/i.test(v.lang));
      if (voice) utterance.voice = voice;
    } else if (hasHindi) {
      utterance.lang = 'hi-IN';
      const voice = voices.find(v => /^hi/i.test(v.lang));
      if (voice) utterance.voice = voice;
    } else {
      utterance.lang = 'en-US';
      const voice = voices.find(v => /Natural|David|Guy|Google/i.test(v.name) && /^en/i.test(v.lang));
      if (voice) utterance.voice = voice;
    }    utterance.onstart = () => { isJarvisSpeaking = true; setStatus('RESPONDING', 'JARVIS is speaking...', true); };
    utterance.onend = () => { isJarvisSpeaking = false; setStatus('LISTENING', 'Ready for your command...'); };
    utterance.onerror = () => { isJarvisSpeaking = false; setStatus('LISTENING', 'Ready for your command...'); };
    synth.speak(utterance);
  }

  $('micButton').addEventListener('click', (event) => { event.stopPropagation(); shouldListen = true; if (!recognition || (!recognitionActive && !recognitionStarting)) setupSpeechRecognition(); });
  window.addEventListener('click', () => { if (!recognitionActive && !recognitionStarting) setupSpeechRecognition(); });
  socket.on('connect', () => { $('connectionLabel').textContent = 'ONLINE'; addNotice('Secure Socket.IO connection established.'); if (!recognitionActive && !recognitionStarting) setupSpeechRecognition(); });
  socket.on('disconnect', () => { $('connectionLabel').textContent = 'RECONNECTING'; setStatus('RECONNECTING', 'Restoring live command channel...'); });
  socket.on('system_status', (data) => {
    $('modelLabel').textContent = `MODEL: ${data.model || 'ONLINE'}`;
    if (data.memory) $('memoryStats').textContent = `MEMORY: ${data.memory.facts || 0} FACTS`;
    if (data.knowledge) {
      $('retrievalStatus').textContent = `KNOWLEDGE: ${data.knowledge.backend === 'disabled' ? 'OFF' : `${data.knowledge.chunks || 0} CHUNKS`}`;
      addNotice(data.knowledge.diagnostic || 'Knowledge status received.');
    }
  });
  socket.on('status', (data) => {
    if (data.state === 'retrieving') {
      $('taskState').textContent = 'RETRIEVING';
      setStatus('RETRIEVING', 'Checking the local knowledge base...');
    } else if (data.state === 'verifying') {
      $('taskState').textContent = 'VERIFYING';
      setStatus('VERIFYING', 'Running the configured answer check...');
    } else if (data.state === 'thinking') {
      $('taskState').textContent = 'ACTIVE';
      setStatus('PROCESSING', 'Understanding your request...');
    }
  });
  socket.on('jarvis_reply', (data) => {
    acknowledgeRequest(data.request_id);
    const text = data.text || '';
    $('jarvisLive').textContent = text;
    $('taskState').textContent = 'IDLE';
    $('taskTitle').textContent = 'Waiting for your command...';
    const sourceCount = Array.isArray(data.sources) ? data.sources.length : 0;
    const verification = data.verification && data.verification.performed ? ' • VERIFIED' : '';
    $('taskDetail').textContent = data.memory_command
      ? 'Memory command completed'
      : `${data.task_type || 'GENERAL'} • ${data.provider || 'local'}${sourceCount ? ` • ${sourceCount} source(s)` : ''}${verification}`;
    $('modelLabel').textContent = `MODEL: ${data.provider && data.model ? `${data.provider}/${data.model}` : 'ONLINE'}`;
    $('retrievalStatus').textContent = data.retrieval_active ? `KNOWLEDGE: ${sourceCount} SOURCES` : 'KNOWLEDGE: INACTIVE';
    addNotice(data.retrieval_active ? `Response grounded in ${sourceCount} retrieved source(s).` : 'Response delivered successfully.');
    speakReply(text);
  });
  socket.on('memory_stats', (data) => { $('memoryStats').textContent = `MEMORY: ${data.facts || 0} FACTS`; });
  socket.on('metrics', (data) => { $('cpuMetric').textContent = `${data.cpu}%`; $('cpuMeter').style.width = `${Math.min(100, Math.max(0, data.cpu))}%`; $('ramMetric').textContent = `${data.ram_used}/${data.ram_total} GB`; $('ramMeter').style.width = `${Math.min(100, Math.max(0, data.ram_pct || 0))}%`; });
  setInterval(() => { socket.emit('get_metrics'); updateClock(); }, 3000);
  updateClock(); updateBattery();
})();

