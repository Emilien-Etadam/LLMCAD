// @file web/js/chat.js
// @brief Chat panel: send prompts to /api/generate, inject code into the
//        editor, trigger preview, with single auto-retry on CadQuery errors.
import { runPreview, clearViewer } from '../main.js';

const api = window.location.origin + '/api/';

// Conversation history sent in /api/generate requests as the `history` field.
// Each entry is {role: 'user'|'assistant', content: string}.
let messages = [];

// Hard guard against double-submits (button + Ctrl+Enter while a request is
// in-flight) and against retry recursion mistakes.
let isWaiting = false;

const thread = document.getElementById('chat-thread');
const input = document.getElementById('chat-input');
const sendBtn = document.getElementById('chat-send');
const newChatBtn = document.getElementById('new-chat-btn');
const codeInput = document.getElementById('code-input');

function scrollThreadToBottom() {
  thread.scrollTop = thread.scrollHeight;
}

function addUserMessage(text) {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-user';
  const body = document.createElement('div');
  body.className = 'msg-text';
  body.textContent = text;
  wrap.appendChild(body);
  thread.appendChild(wrap);
  scrollThreadToBottom();
  return wrap;
}

function addAssistantCodeMessage(code) {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-assistant';
  const label = document.createElement('div');
  label.className = 'msg-label';
  label.textContent = 'Build123d';
  const pre = document.createElement('pre');
  pre.className = 'msg-code';
  pre.textContent = code;
  wrap.appendChild(label);
  wrap.appendChild(pre);
  thread.appendChild(wrap);
  scrollThreadToBottom();
  return wrap;
}

function addSystemMessage(text, opts = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-system';
  if (opts.error) wrap.classList.add('msg-error');
  const body = document.createElement('div');
  body.className = 'msg-text';
  body.textContent = text;
  wrap.appendChild(body);
  thread.appendChild(wrap);
  scrollThreadToBottom();
  return wrap;
}

function addLoadingIndicator() {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-assistant msg-loading';
  for (let i = 0; i < 3; i += 1) {
    const dot = document.createElement('span');
    dot.className = 'dot';
    wrap.appendChild(dot);
  }
  thread.appendChild(wrap);
  scrollThreadToBottom();
  return wrap;
}

function setBusy(busy) {
  isWaiting = busy;
  if (busy) {
    sendBtn.classList.add('button-disabled');
    sendBtn.disabled = true;
  } else {
    sendBtn.classList.remove('button-disabled');
    sendBtn.disabled = false;
  }
}

async function callGenerate(prompt) {
  const currentCode = codeInput.value;
  const response = await fetch(api + 'generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, history: messages, currentCode })
  });
  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = null;
  }
  return { ok: response.ok, status: response.status, data };
}

/**
 * Send a prompt to /api/generate, render the response, run /api/preview,
 * and (optionally) auto-retry once if the preview fails.
 *
 * @param {string|null} promptOverride - if non-null, use this string as prompt
 *                                       (used by the auto-retry path).
 * @param {boolean} isRetry - true on the second attempt; disables further retries
 *                            and adds a small "auto-fix" indicator.
 */
async function sendMessage(promptOverride = null, isRetry = false) {
  if (isWaiting) return;

  const prompt = (promptOverride !== null) ? promptOverride : input.value.trim();
  if (!prompt) return;

  setBusy(true);

  if (isRetry) {
    addSystemMessage('Tentative de correction automatique…');
  }

  // Display the user message and append it to the history that we send to /generate.
  addUserMessage(prompt);
  messages.push({ role: 'user', content: prompt });

  if (!isRetry) {
    input.value = '';
  }

  const loadingEl = addLoadingIndicator();

  let assistantCode = null;
  try {
    const { ok, data } = await callGenerate(prompt);
    loadingEl.remove();

    if (!ok || !data || !data.success) {
      const errMsg = (data && data.error) || `LLM generation failed (HTTP error)`;
      addSystemMessage(`Erreur LLM : ${errMsg}`, { error: true });
      setBusy(false);
      return;
    }

    assistantCode = (data.code || '').toString();
    addAssistantCodeMessage(assistantCode);
    messages.push({ role: 'assistant', content: assistantCode });

    // Inject + auto-preview.
    codeInput.value = assistantCode;
  } catch (err) {
    loadingEl.remove();
    addSystemMessage(`Erreur réseau : ${err && err.message ? err.message : String(err)}`, { error: true });
    setBusy(false);
    return;
  }

  // Free the busy lock before running preview, otherwise the recursive retry
  // call below would early-return on isWaiting.
  setBusy(false);

  let previewResult;
  try {
    previewResult = await runPreview();
  } catch (err) {
    previewResult = { success: false, message: err && err.message ? err.message : String(err) };
  }

  if (previewResult.success) return;

  addSystemMessage(`Erreur d'exécution : ${previewResult.message}`, { error: true });

  if (isRetry) {
    addSystemMessage('Le retry automatique a aussi échoué. À vous d\'ajuster le prompt.', { error: true });
    return;
  }

  // Single auto-retry. We construct a follow-up prompt with the runtime error
  // so the LLM can patch the previous answer (currentCode is whatever we just
  // injected, so the model has full context).
  const retryPrompt = `The code produced this error: ${previewResult.message}. Fix it.`;
  await sendMessage(retryPrompt, true);
}

sendBtn.addEventListener('click', () => sendMessage());

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendMessage();
  }
});

newChatBtn.addEventListener('click', () => {
  if (isWaiting) return;
  messages = [];
  thread.innerHTML = '';
  codeInput.value = '';
  clearViewer();
});
