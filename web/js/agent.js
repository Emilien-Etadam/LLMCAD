const agentPrompt = document.getElementById('agent-prompt');
const agentGenerateBtn = document.getElementById('agent-generate-btn');
const agentIterations = document.getElementById('agent-iterations');
const agentStatus = document.getElementById('agent-status');
const agentLoadCodeBtn = document.getElementById('agent-load-code-btn');
const codeInput = document.getElementById('code-input');

let isAgentRunning = false;
let finalCode = '';
let iterationState = new Map();

function setAgentBusy(busy) {
  isAgentRunning = busy;
  agentGenerateBtn.disabled = busy;
  if (busy) {
    agentGenerateBtn.classList.add('button-disabled');
  } else {
    agentGenerateBtn.classList.remove('button-disabled');
  }
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
  return { details, summary, code, error };
}

function updateIterationStatus(n, status) {
  const entry = iterationState.get(n);
  if (!entry) return;
  entry.summary.textContent = `Itération ${n} - ${status}`;
}

function applyAgentEvent(event) {
  if (event.type === 'iteration_start') {
    iterationState.set(event.n, createIterationNode(event.n));
    agentStatus.textContent = `Itération ${event.n} démarrée`;
    return;
  }

  const currentN = Math.max(...Array.from(iterationState.keys(), (k) => Number(k)));
  const current = iterationState.get(currentN);

  if (event.type === 'llm_token') {
    agentStatus.textContent = `Génération LLM en cours (itération ${currentN})...`;
    return;
  }

  if (event.type === 'code_extracted' && current) {
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
