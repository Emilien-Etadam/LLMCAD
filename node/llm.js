/// @file llm.js
/// @brief vLLM (OpenAI-compatible) client for CadQuery code generation
/// @author LLMCAD

const axios = require('axios');

const VLLM_URL = (process.env.VLLM_URL || 'http://192.168.30.121:8000/v1').replace(/\/+$/, '');
const VLLM_MODEL = process.env.VLLM_MODEL || 'Qwen/Qwen2.5-Coder-32B-Instruct';
const VLLM_API_KEY = process.env.VLLM_API_KEY || '';
const VLLM_TIMEOUT_MS = 30000;

const SYSTEM_PROMPT = `You are a CadQuery code generator. Output ONLY valid Python code, nothing else. No explanations, no markdown, no code fences.
Rules:
- Always import cadquery as cq
- The final object MUST be assigned to a variable named "result"
- Never use show_object() or any display function
- Use only cadquery API: Workplane, Sketch, Assembly, and their methods
- If the user provides existing code and asks for a modification, return the full modified code, not a diff`;

/**
 * Strip markdown code fences and any preamble before the first cadquery import.
 * The system prompt forbids fences, but some models still emit them.
 */
function cleanCode(raw) {
  if (typeof raw !== 'string') return '';
  let code = raw.trim();

  const fenceMatch = code.match(/```(?:python|py)?\s*\n?([\s\S]*?)\n?```/i);
  if (fenceMatch) {
    code = fenceMatch[1].trim();
  } else {
    code = code.replace(/^```(?:python|py)?\s*\n?/i, '');
    code = code.replace(/\n?```\s*$/i, '');
    code = code.trim();
  }

  const importIdx = code.search(/^\s*import\s+cadquery\b/m);
  if (importIdx > 0) {
    code = code.slice(importIdx);
  } else if (importIdx === -1) {
    const fromIdx = code.search(/^\s*from\s+cadquery\b/m);
    if (fromIdx > 0) {
      code = code.slice(fromIdx);
    }
  }

  // The Python sandbox (cadquery/server.py) pre-injects `cq`, `np`, `math` and
  // strips `__import__` from builtins. A literal `import cadquery as cq` therefore
  // raises "__import__ not found" at runtime. The system prompt still instructs
  // the model to write the import for human readability/portability, but we strip
  // any top-level cadquery import lines before returning the code to the client.
  code = code
    .split('\n')
    .filter(line => !/^\s*import\s+cadquery(\s+as\s+\w+)?\s*(#.*)?$/.test(line)
                 && !/^\s*from\s+cadquery(\.[\w.]+)?\s+import\s+/.test(line))
    .join('\n');

  return code.trim();
}

/**
 * Build the chat-completions message array.
 *
 * Layout:
 *   [0]            system prompt
 *   [1..n-2]       prior conversation history (alternating user/assistant)
 *   [n-1] (opt.)   user message containing currentCode
 *   [n]            new user prompt
 */
function buildMessages(prompt, history, currentCode) {
  const messages = [{ role: 'system', content: SYSTEM_PROMPT }];

  if (Array.isArray(history)) {
    for (const m of history) {
      if (!m || typeof m !== 'object') continue;
      const role = m.role;
      const content = typeof m.content === 'string' ? m.content : '';
      if ((role === 'user' || role === 'assistant') && content.length > 0) {
        messages.push({ role, content });
      }
    }
  }

  if (typeof currentCode === 'string' && currentCode.trim().length > 0) {
    messages.push({
      role: 'user',
      content: `Current CadQuery code:\n\`\`\`python\n${currentCode}\n\`\`\``
    });
  }

  messages.push({ role: 'user', content: String(prompt || '') });
  return messages;
}

/**
 * Call the remote vLLM server and return cleaned CadQuery code.
 * @throws {Error} on network/timeout/HTTP failures, or empty model output.
 */
async function generateCadQuery(prompt, history, currentCode) {
  if (typeof prompt !== 'string' || prompt.trim().length === 0) {
    const err = new Error('Prompt must be a non-empty string');
    err.status = 400;
    throw err;
  }

  const messages = buildMessages(prompt, history, currentCode);

  const headers = { 'Content-Type': 'application/json' };
  if (VLLM_API_KEY) headers.Authorization = `Bearer ${VLLM_API_KEY}`;

  let response;
  try {
    response = await axios.post(
      `${VLLM_URL}/chat/completions`,
      {
        model: VLLM_MODEL,
        messages,
        temperature: 0.2,
        max_tokens: 4096,
        stream: false,
        // Qwen3-specific: disable the chain-of-thought "thinking" channel so the
        // assistant's content field directly contains the requested code. The
        // server silently ignores this for chat templates that don't use it.
        chat_template_kwargs: { enable_thinking: false }
      },
      {
        timeout: VLLM_TIMEOUT_MS,
        headers
      }
    );
  } catch (err) {
    if (err.code === 'ECONNABORTED') {
      const e = new Error(`vLLM request timed out after ${VLLM_TIMEOUT_MS}ms (${VLLM_URL})`);
      e.status = 504;
      throw e;
    }
    if (err.response) {
      const detail = err.response.data && (err.response.data.error?.message || err.response.data.message)
        || JSON.stringify(err.response.data);
      const e = new Error(`vLLM HTTP ${err.response.status}: ${detail}`);
      e.status = 502;
      throw e;
    }
    const e = new Error(`vLLM network error (${VLLM_URL}): ${err.message}`);
    e.status = 502;
    throw e;
  }

  const content = response.data?.choices?.[0]?.message?.content;
  if (typeof content !== 'string' || content.trim().length === 0) {
    const e = new Error('vLLM returned empty content');
    e.status = 502;
    throw e;
  }

  const code = cleanCode(content);
  if (code.length === 0) {
    const e = new Error('vLLM response did not contain valid code after cleanup');
    e.status = 502;
    throw e;
  }
  return code;
}

module.exports = {
  generateCadQuery,
  cleanCode,
  buildMessages,
  SYSTEM_PROMPT,
  VLLM_URL,
  VLLM_MODEL,
  VLLM_API_KEY_SET: VLLM_API_KEY.length > 0
};
