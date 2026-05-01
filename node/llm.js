/// @file llm.js
/// @brief vLLM (OpenAI-compatible) helpers + agentic Build123d loop

const OpenAI = require('openai');

const VLLM_BASE_URL = (process.env.VLLM_BASE_URL || process.env.VLLM_URL || 'http://192.168.30.121:8000/v1').replace(/\/+$/, '');
const VLLM_MODEL = process.env.VLLM_MODEL || '/data/models/qwen3-32b-fp8';
const VLLM_API_KEY = process.env.VLLM_API_KEY || 'EMPTY';
const AGENT_MAX_ITERATIONS = parseInt(process.env.AGENT_MAX_ITERATIONS || '5', 10);
const AGENT_REQUEST_TIMEOUT_MS = parseInt(process.env.AGENT_REQUEST_TIMEOUT_MS || '30000', 10);

const CADQUERY_HOST = process.env.CADQUERY_HOST || '127.0.0.1';
const CADQUERY_PORT = parseInt(process.env.CADQUERY_PORT || '5002', 10);
const CAD_SERVER_URL = process.env.CAD_SERVER_URL || `http://${CADQUERY_HOST}:${CADQUERY_PORT}`;

const SYSTEM_PROMPT = `Tu es un assistant CAO expert en Build123d (Python).

Génère UNIQUEMENT du code Python valide qui :
- Importe avec: from build123d import *
- Termine TOUJOURS par une variable nommée "result" de type Part, Compound, ou Solid
- Utilise le mode algébrique (operators +/-/*) plutôt que les Builder contexts
- N'imprime rien, ne lit aucun fichier, ne fait aucun import autre que build123d

Format de réponse OBLIGATOIRE :
\`\`\`python
from build123d import *

# ton code ici
result = ...
\`\`\`

Aucun texte avant ou après le bloc de code.

Exemples corrects :

Demande: "boîte 50x30x10 avec congé 2mm"
Réponse:
\`\`\`python
from build123d import *

result = Box(50, 30, 10)
result = fillet(result.edges(), radius=2)
\`\`\`

Demande: "tube creux extérieur 20mm intérieur 15mm hauteur 40mm"
Réponse:
\`\`\`python
from build123d import *

result = Cylinder(radius=10, height=40) - Cylinder(radius=7.5, height=40)
\`\`\`

Demande: "plaque 100x60x5 avec 4 trous traversants diamètre 6 aux coins, marge 10mm"
Réponse:
\`\`\`python
from build123d import *

plate = Box(100, 60, 5)
holes = Compound([
    Pos(40, 20, 0) * Cylinder(radius=3, height=5),
    Pos(-40, 20, 0) * Cylinder(radius=3, height=5),
    Pos(40, -20, 0) * Cylinder(radius=3, height=5),
    Pos(-40, -20, 0) * Cylinder(radius=3, height=5),
])
result = plate - holes
\`\`\`

Si on te donne une erreur d'exécution, corrige le code en gardant la même intention.
Réponds avec le code corrigé, toujours dans le même format.`;

/**
 * @param {string} text
 * @returns {string|null}
 */
function extractPythonCode(text) {
  if (typeof text !== 'string') return null;
  const match = text.match(/```python\s*\n([\s\S]*?)\n```/i);
  return match ? match[1].trim() : null;
}

/**
 * @param {number} ms
 * @returns {AbortController}
 */
function buildTimeoutController(ms) {
  const controller = new AbortController();
  setTimeout(() => controller.abort(new Error(`timeout_after_${ms}ms`)), ms);
  return controller;
}

/**
 * @param {string} message
 * @returns {{error: string, traceback: string}}
 */
function splitErrorAndTraceback(message) {
  const raw = typeof message === 'string' ? message : String(message || '');
  const lines = raw.split('\n');
  const error = lines[0] || 'Execution error';
  const traceback = lines.slice(1).join('\n').trim();
  return { error, traceback };
}

/**
 * @typedef {Object} PreviewSuccess
 * @property {true} ok
 * @property {{vertices:number[], faces:number[], objectCount?:number}} preview
 *
 * @typedef {Object} PreviewError
 * @property {false} ok
 * @property {string} error
 * @property {string} traceback
 */

class CADAgent {
  /**
   * @param {{
   *  vllmBaseUrl?: string,
   *  model?: string,
   *  cadServerUrl?: string,
   *  maxIterations?: number,
   *  requestTimeout?: number
   * }} opts
   */
  constructor(opts = {}) {
    this.vllmBaseUrl = (opts.vllmBaseUrl || VLLM_BASE_URL).replace(/\/+$/, '');
    this.model = opts.model || VLLM_MODEL;
    this.cadServerUrl = (opts.cadServerUrl || CAD_SERVER_URL).replace(/\/+$/, '');
    this.maxIterations = opts.maxIterations || AGENT_MAX_ITERATIONS;
    this.requestTimeout = opts.requestTimeout || AGENT_REQUEST_TIMEOUT_MS;
    this.client = new OpenAI({
      apiKey: VLLM_API_KEY,
      baseURL: this.vllmBaseUrl,
      timeout: this.requestTimeout
    });
  }

  /**
   * @param {string} code
   * @returns {Promise<PreviewSuccess|PreviewError>}
   */
  async executePreview(code) {
    const controller = buildTimeoutController(this.requestTimeout);
    let response;
    try {
      response = await fetch(`${this.cadServerUrl}/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
        signal: controller.signal
      });
    } catch (err) {
      return { ok: false, error: `preview_request_failed: ${err.message}`, traceback: '' };
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch (_err) {
      payload = null;
    }

    if (response.ok && payload && payload.data && payload.data !== 'None') {
      return { ok: true, preview: payload.data };
    }
    const composed = payload && payload.message ? payload.message : `preview_http_${response.status}`;
    const parsed = splitErrorAndTraceback(composed);
    return { ok: false, error: parsed.error, traceback: parsed.traceback };
  }

  /**
   * @param {string} userPrompt
   */
  async *run(userPrompt) {
    const messages = [
      { role: 'system', content: SYSTEM_PROMPT },
      { role: 'user', content: userPrompt }
    ];

    let totalPromptTokens = 0;
    let totalCompletionTokens = 0;
    let lastError = '';

    for (let n = 1; n <= this.maxIterations; n += 1) {
      yield { type: 'iteration_start', n };

      let fullResponse = '';
      let fullReasoning = '';
      let usage = null;
      const stream = await this.client.chat.completions.create({
        model: this.model,
        messages,
        stream: true,
        stream_options: { include_usage: true },
        temperature: 0.2,
        max_tokens: 4096
      });

      for await (const chunk of stream) {
        const delta = chunk.choices[0]?.delta;
        if (!delta) {
          if (chunk.usage) usage = chunk.usage;
          continue;
        }

        const reasoningToken = delta.reasoning || '';
        if (reasoningToken) {
          fullReasoning += reasoningToken;
          yield { type: 'reasoning_token', token: reasoningToken };
        }

        const contentToken = delta.content || '';
        if (contentToken) {
          fullResponse += contentToken;
          yield { type: 'llm_token', token: contentToken };
        }

        if (chunk.usage) usage = chunk.usage;
      }

      if (usage) {
        totalPromptTokens += usage.prompt_tokens || 0;
        totalCompletionTokens += usage.completion_tokens || 0;
      }
      console.log(
        `[agent] iter=${n} reasoning_chars=${fullReasoning.length} content_chars=${fullResponse.length}`
      );
      console.log(
        `[agent] iter=${n} tokens prompt=${usage?.prompt_tokens ?? 0} completion=${usage?.completion_tokens ?? 0} total_prompt=${totalPromptTokens} total_completion=${totalCompletionTokens} grand_total=${totalPromptTokens + totalCompletionTokens}`
      );

      const code = extractPythonCode(fullResponse);
      if (!code) {
        lastError = 'Code Python non trouvé dans la réponse';
        yield { type: 'execution_error', error: lastError, traceback: '' };
        messages.push({ role: 'assistant', content: fullResponse });
        messages.push({ role: 'user', content: "Tu n'as pas inclus de bloc ```python ... ```. Recommence." });
        continue;
      }

      yield { type: 'code_extracted', code };
      yield { type: 'execution_start' };

      const result = await this.executePreview(code);
      if (result.ok) {
        yield { type: 'execution_success', preview: result.preview };
        yield { type: 'final_success', code, preview: result.preview };
        return;
      }

      lastError = result.error;
      yield { type: 'execution_error', error: result.error, traceback: result.traceback };
      messages.push({ role: 'assistant', content: fullResponse });
      messages.push({
        role: 'user',
        content: `Le code a planté.\n\nErreur: ${result.error}\n\nTraceback:\n${result.traceback}\n\nCorrige le code.`
      });
    }

    yield { type: 'final_failure', reason: 'max_iterations', lastError: lastError || 'epuise' };
  }
}

module.exports = {
  CADAgent,
  extractPythonCode,
  SYSTEM_PROMPT,
  VLLM_URL: VLLM_BASE_URL,
  VLLM_MODEL
};
