const agentPrompt = document.getElementById('agent-prompt');
const agentGenerateBtn = document.getElementById('agent-generate-btn');
const agentIterations = document.getElementById('agent-iterations');
const agentStatus = document.getElementById('agent-status');
const agentLoadCodeBtn = document.getElementById('agent-load-code-btn');
const codeInput = document.getElementById('code-input');

let isAgentRunning = false;
let finalCode = '';
let iterationState = new Map();
let ragState = null;

function ensureRagSection() {
  if (ragState) return ragState;
  const details = document.createElement('details');
  details.className = 'agent-rag';
  details.open = false;

  const summary = document.createElement('summary');
  summary.textContent = 'Documentation référencée';

  const meta = document.createElement('div');
  meta.className = 'rag-meta';
  meta.textContent = '';

  const list = document.createElement('div');
  list.className = 'rag-list';

  details.appendChild(summary);
  details.appendChild(meta);
  details.appendChild(list);
  agentIterations.prepend(details);
  ragState = { details, summary, meta, list };
  return ragState;
}

function setAgentBusy(busy) {
  isAgentRunning = busy;
  agentGenerateBtn.disabled = busy;
  if (busy) {
    agentGenerateBtn.classList.add('button-disabled');
  } else {
    agentGenerateBtn.classList.remove('button-disabled');
  }
}

function ensureThinkingSection(entry) {
  if (entry.thinkingPre) return;
  const thinkingDetails = document.createElement('details');
  thinkingDetails.className = 'agent-thinking';
  thinkingDetails.open = true;

  const thinkingSummary = document.createElement('summary');
  thinkingSummary.textContent = 'Réflexion du modèle';

  const thinkingPre = document.createElement('pre');
  thinkingPre.className = 'thinking-content';
  thinkingPre.textContent = '';

  thinkingDetails.appendChild(thinkingSummary);
  thinkingDetails.appendChild(thinkingPre);
  entry.body.insertBefore(thinkingDetails, entry.code);

  entry.thinkingDetails = thinkingDetails;
  entry.thinkingPre = thinkingPre;
  entry.thinkingKeptOpenByUser = false;

  thinkingDetails.addEventListener('toggle', () => {
    if (entry.thinkingToggleFromProgram) return;
    entry.thinkingKeptOpenByUser = thinkingDetails.open;
  });
}

function createIterationNode(n) {
  const details = document.createElement('details');
  details.className = 'agent-iteration';
  details.open = true;
  details.dataset.iteration = String(n);

  const summary = document.createElement('summary');
  summary.textContent = `Itération ${n} - en cours`;

  const body = document.createElement('div');
  body.className = 'agent-iteration-body';

  const code = document.createElement('pre');
  code.className = 'agent-code';
  code.textContent = '';

  const error = document.createElement('div');
  error.className = 'agent-error';
  error.textContent = '';

  body.appendChild(code);
  body.appendChild(error);
  details.appendChild(summary);
  details.appendChild(body);
  agentIterations.prepend(details);
  return {
    details,
    summary,
    body,
    code,
    error,
    thinkingDetails: null,
    thinkingPre: null,
    thinkingKeptOpenByUser: false,
    thinkingToggleFromProgram: false
  };
}

function updateIterationStatus(n, status) {
  const entry = iterationState.get(n);
  if (!entry) return;
  entry.summary.textContent = `Itération ${n} - ${status}`;
}

function applyAgentEvent(event) {
  if (event.type === 'rag_start') {
    const rag = ensureRagSection();
    rag.meta.textContent = 'Recherche dans la documentation...';
    rag.list.innerHTML = '';
    agentStatus.textContent = 'Recherche dans la documentation...';
    return;
  }

  if (event.type === 'rag_retrieved') {
    const rag = ensureRagSection();
    const count = Number(event.chunks_count || 0);
    const reason = event.reason || 'unknown';
    rag.meta.textContent = `${count} chunks trouvés (raison: ${reason})`;
    rag.list.innerHTML = '';

    const chunks = Array.isArray(event.chunks) ? event.chunks : [];
    for (const chunk of chunks) {
      const item = document.createElement('div');
      item.className = 'rag-item';

      const header = document.createElement('div');
      header.className = 'rag-item-header';
      const source = chunk.source_file || 'unknown';
      const score = typeof chunk.score === 'number' ? chunk.score.toFixed(3) : 'n/a';
      header.textContent = `Source: ${source} | score: ${score}`;

      const preview = document.createElement('pre');
      preview.className = 'rag-item-preview';
      preview.textContent = chunk.text_preview || '';

      item.appendChild(header);
      item.appendChild(preview);
      rag.list.appendChild(item);
    }
    return;
  }

  if (event.type === 'iteration_start') {
    iterationState.set(event.n, createIterationNode(event.n));
    agentStatus.textContent = `Itération ${event.n} démarrée`;
    return;
  }

  const currentN = iterationState.size
    ? Math.max(...Array.from(iterationState.keys(), (k) => Number(k)))
    : 0;
  const current = iterationState.get(currentN);

  if (event.type === 'llm_token') {
    agentStatus.textContent = `Génération LLM en cours (itération ${currentN})...`;
    return;
  }

  if (event.type === 'reasoning_token' && current) {
    agentStatus.textContent = `Réflexion du modèle (itération ${currentN})...`;
    ensureThinkingSection(current);
    current.thinkingPre.textContent += event.token || '';
    if (current.thinkingDetails && current.thinkingDetails.open) {
      current.thinkingPre.scrollTop = current.thinkingPre.scrollHeight;
    }
    return;
  }

  if (event.type === 'code_extracted' && current) {
    if (current.thinkingDetails && !current.thinkingKeptOpenByUser) {
      current.thinkingToggleFromProgram = true;
      current.thinkingDetails.open = false;
      current.thinkingToggleFromProgram = false;
    }
    current.code.textContent = event.code || '';
    updateIterationStatus(currentN, 'code extrait');
    return;
  }

  if (event.type === 'execution_start') {
    agentStatus.textContent = `Exécution /preview (itération ${currentN})...`;
    updateIterationStatus(currentN, 'exécution');
    return;
  }

  if (event.type === 'execution_success' && current) {
    updateIterationStatus(currentN, 'succes ✓');
    current.error.textContent = '';
    return;
  }

  if (event.type === 'execution_error' && current) {
    updateIterationStatus(currentN, 'erreur ✗');
    const traceback = event.traceback ? `\n\nTraceback:\n${event.traceback}` : '';
    current.error.textContent = `Erreur: ${event.error || 'unknown'}${traceback}`;
    return;
  }

  if (event.type === 'final_success') {
    finalCode = event.code || '';
    agentLoadCodeBtn.disabled = !finalCode;
    agentStatus.textContent = 'Terminé: succès final';
    return;
  }

  if (event.type === 'final_failure') {
    agentStatus.textContent = `Terminé: échec (${event.reason || 'unknown'})`;
    return;
  }

  if (event.type === 'fatal_error') {
    agentStatus.textContent = `Erreur fatale: ${event.error || 'unknown'}`;
  }
}

async function streamAgent(prompt) {
  const response = await fetch('/api/agent', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt })
  });
  if (!response.ok || !response.body) {
    let text = '';
    try {
      text = await response.text();
    } catch (_err) {
      text = '';
    }
    throw new Error(`HTTP ${response.status}: ${text || 'agent request failed'}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split('\n\n');
    buffer = events.pop() || '';
    for (const evt of events) {
      const line = evt.split('\n').find((l) => l.startsWith('data: '));
      if (!line) continue;
      const payload = line.slice(6);
      try {
        const parsed = JSON.parse(payload);
        applyAgentEvent(parsed);
      } catch (_err) {
        // Ignore malformed chunk and continue streaming.
      }
    }
  }
}

agentGenerateBtn.addEventListener('click', async () => {
  if (isAgentRunning) return;
  const prompt = (agentPrompt.value || '').trim();
  if (!prompt) return;

  iterationState = new Map();
  ragState = null;
  finalCode = '';
  agentIterations.innerHTML = '';
  agentLoadCodeBtn.disabled = true;
  setAgentBusy(true);
  agentStatus.textContent = 'Connexion à l’agent...';

  try {
    await streamAgent(prompt);
  } catch (err) {
    agentStatus.textContent = `Erreur: ${err.message}`;
  } finally {
    setAgentBusy(false);
  }
});

agentLoadCodeBtn.addEventListener('click', () => {
  if (!finalCode) return;
  codeInput.value = finalCode;
  agentStatus.textContent = 'Code chargé dans l’éditeur';
});
