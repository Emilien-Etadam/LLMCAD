/// @file llm.js
/// @brief vLLM (OpenAI-compatible) client for CadQuery code generation
/// @author LLMCAD

const axios = require('axios');

const VLLM_URL = (process.env.VLLM_URL || 'http://192.168.30.121:8000/v1').replace(/\/+$/, '');
const VLLM_MODEL = process.env.VLLM_MODEL || 'Qwen/Qwen2.5-Coder-32B-Instruct';
const VLLM_API_KEY = process.env.VLLM_API_KEY || '';
const VLLM_TIMEOUT_MS = 30000;

const SYSTEM_PROMPT = `You are a CadQuery code generator. You output ONLY valid Python code. No explanations, no markdown, no code fences, no comments unless they clarify complex geometry logic.

HARD RULES:
- Always start with: import cadquery as cq
- You may also import math and typing if needed. No other imports.
- The final 3D object MUST be assigned to a variable named "result"
- Never use show_object(), cq.exporters, or any display/export function
- Never use eval(), exec(), open(), os, sys, subprocess, or any system call
- Never access dunder attributes (__class__, __bases__, __import__, etc.)
- If the user provides existing code and asks for a modification, return the FULL modified code, not a diff or partial snippet

CADQUERY API REFERENCE (use only these):

Workplane creation:
  cq.Workplane("XY" | "XZ" | "YZ")
  .transformed(offset=(x,y,z), rotate=(rx,ry,rz))

2D Primitives (on workplane):
  .rect(xLen, yLen, centered=True)
  .circle(radius)
  .ellipse(x_radius, y_radius)
  .polygon(nSides, diameter)
  .slot2D(length, diameter, angle=0)
  .text(txt, fontsize, distance, cut=True/False, font="Arial")

2D Drawing:
  .moveTo(x, y)
  .lineTo(x, y)
  .line(dx, dy)
  .hLine(distance), .vLine(distance)
  .hLineTo(x), .vLineTo(y)
  .threePointArc(p1, p2)
  .sagittaArc(endPoint, sag)
  .radiusArc(endPoint, radius)
  .tangentArcPoint(endpoint)
  .spline(listOfXYTuple)
  .polyline(listOfXYTuple)
  .close()
  .mirrorX(), .mirrorY()
  .offset2D(distance)
  .wire()

3D Operations:
  .extrude(distance, combine=True, both=False)
  .revolve(angleDegrees=360, axisStart=(0,0,0), axisEnd=(0,1,0))
  .sweep(path, multisection=False)
  .loft(ruled=False)
  .shell(thickness) — hollows the solid
  .cut(other) — boolean subtract
  .union(other) — boolean add
  .intersect(other) — boolean intersect
  .hole(diameter, depth=None)
  .cboreHole(diameter, cboreDiameter, cboreDepth, depth=None)
  .cskHole(diameter, cskDiameter, cskAngle, depth=None)

Transforms:
  .translate((x, y, z))
  .rotateAboutCenter((ax, ay, az), angleDegrees)
  .rotate((0,0,0), (0,0,1), angleDegrees)
  .mirror("XY" | "XZ" | "YZ")

Edge/Face Operations:
  .edges(selector) — e.g. "|Z", ">Z", "<Z"
  .faces(selector) — e.g. ">Z", "<X", "+Y"
  .fillet(radius)
  .chamfer(length)
  .workplane(offset=0)

Selectors (string):
  ">X", "<X", ">Y", "<Y", ">Z", "<Z" — max/min along axis
  "|X", "|Y", "|Z" — parallel to axis
  "#X", "#Y", "#Z" — perpendicular to axis
  "not(<selector>)" — negate
  ">Z[-2]" — second from top
  Combine with and/or: ">Z and |X"

Patterns:
  .rarray(xSpacing, ySpacing, xCount, yCount) — rectangular array of points
  .polarArray(radius, startAngle, angle, count) — polar array of points
  .pushPoints([(x1,y1), (x2,y2), ...]) — arbitrary point array

Workplane chaining:
  .faces(">Z").workplane() — new workplane on top face
  .faces("<Z").workplane() — new workplane on bottom face
  .center(x, y) — shift workplane origin

Assembly (when multiple parts):
  assy = cq.Assembly()
  assy.add(part, name="name", loc=cq.Location((x,y,z), (rx,ry,rz)))
  result = assy

MODELING PATTERNS:

Pattern 1 — Base with pocket:
  result = (cq.Workplane("XY").rect(100,60).extrude(20)
    .faces(">Z").workplane().rect(80,40).cutBlind(-15))

Pattern 2 — Bolt hole pattern:
  result = (cq.Workplane("XY").circle(50).extrude(10)
    .faces(">Z").workplane()
    .polarArray(35, 0, 360, 8).hole(5))

Pattern 3 — Profile extrusion:
  result = (cq.Workplane("XY")
    .moveTo(0,0).lineTo(50,0).lineTo(50,10)
    .lineTo(30,10).lineTo(30,40).lineTo(20,40)
    .lineTo(20,10).lineTo(0,10).close()
    .extrude(80))

Pattern 4 — Revolution:
  result = (cq.Workplane("XZ")
    .moveTo(10,0).lineTo(20,0).lineTo(20,50)
    .lineTo(15,55).lineTo(10,55).close()
    .revolve(360, (0,0,0), (0,1,0)))

Pattern 5 — Parametric with loops:
  import math
  result = cq.Workplane("XY").circle(50).extrude(5)
  for i in range(8):
      a = math.radians(i * 45)
      result = result.cut(
          cq.Workplane("XY")
          .center(35 * math.cos(a), 35 * math.sin(a))
          .circle(6).extrude(5))

COMMON MISTAKES TO AVOID:
- Do not chain .hole() after .edges().fillet() — do fillet last or use .faces(">Z").workplane() before .hole()
- Do not use .extrude() on a 3D object — it works on 2D wire/face only
- Do not forget .close() when drawing a profile with lineTo/line
- .shell() removes a face first; call it on the solid, not on a workplane
- .fillet() radius must be less than half the smallest edge length
- When cutting holes in a pattern, .pushPoints() or loops are more reliable than .rarray() for non-grid layouts
- For assemblies, each part must be a separate cq.Workplane chain, then added to cq.Assembly

OUTPUT FORMAT:
Return the complete Python script ready to execute. Start with imports, define any helper functions, then build the geometry, and end with the result assignment. Nothing else.`;

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
