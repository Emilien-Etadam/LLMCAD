/// @file server.js
/// @brief Node server: serve frontend (web/) and proxy CadQuery API (bare-metal)
/// @author 30hours (bare-metal adaptation)

const path = require('path');
const fs = require('fs');
const fsp = fs.promises;

require('dotenv').config({ path: path.resolve(__dirname, '..', '.env') });

const express = require('express');
const rate_limit = require('express-rate-limit');
const cors = require('cors');
const RequestQueue = require('./RequestQueue');
const { generateCadQuery, VLLM_URL, VLLM_MODEL } = require('./llm');

const NODE_HOST = process.env.NODE_HOST || '0.0.0.0';
const NODE_PORT = parseInt(process.env.NODE_PORT || '49157', 10);
const WEB_DIR = path.resolve(__dirname, '..', 'web');
const LOG_DIR = path.resolve(__dirname, '..', 'logs');

if (!fs.existsSync(LOG_DIR)) {
  fs.mkdirSync(LOG_DIR, { recursive: true });
}

const app = express();
app.set('trust proxy', 1);

const limiter = rate_limit({
  // 10 minutes
  windowMs: 10 * 60 * 1000,
  max: 30,
  message: {
    data: 'none',
    message: 'Rate limited (>30 requests in 10 mins)'
  }
});

app.use(cors());
app.use(express.json({ limit: '256kb' }));

const VALID_ENDPOINTS = ['preview', 'stl', 'step'];
const requestQueue = new RequestQueue();

app.get('/test', (req, res) => {
  res.send('Node server is running');
});

async function appendRequestLog(timestamp, fields) {
  const lines = ['{', `  "timestamp": "${timestamp}",`];
  const entries = Object.entries(fields);
  entries.forEach(([key, value], idx) => {
    const sep = idx === entries.length - 1 ? '' : ',';
    if (typeof value === 'string' && value.includes('\n')) {
      const indented = value.split('\n').map(l => '    ' + l).join('\n');
      lines.push(`  "${key}":`);
      lines.push(indented + sep);
    } else {
      lines.push(`  "${key}": ${JSON.stringify(value)}${sep}`);
    }
  });
  lines.push('}\n');
  const formatted = lines.join('\n');
  try {
    const logFile = path.join(LOG_DIR, `requests-${timestamp.split('T')[0]}.log`);
    await fsp.appendFile(logFile, formatted, 'utf8');
  } catch (error) {
    console.error('Error logging request:', error);
  }
}

app.post('/api/generate', limiter, async (req, res) => {
  const timestamp = new Date().toISOString();
  const body = req.body || {};
  const prompt = typeof body.prompt === 'string' ? body.prompt : '';
  const history = Array.isArray(body.history) ? body.history : [];
  const currentCode = typeof body.currentCode === 'string' ? body.currentCode : '';
  const ip = req.headers['x-real-ip'] || req.ip;

  await appendRequestLog(timestamp, {
    endpoint: 'generate',
    prompt,
    history_len: history.length,
    current_code_len: currentCode.length,
    ip
  });

  if (!prompt || prompt.trim().length === 0) {
    return res.status(400).json({ success: false, error: 'Missing or empty "prompt"' });
  }

  try {
    const code = await generateCadQuery(prompt, history, currentCode);
    await appendRequestLog(new Date().toISOString(), {
      endpoint: 'generate-result',
      ok: true,
      code_len: code.length,
      ip
    });
    return res.json({ success: true, code });
  } catch (error) {
    const status = error.status || 500;
    const message = error.message || 'LLM generation failed';
    console.log('[ERROR][generate] ', message);
    await appendRequestLog(new Date().toISOString(), {
      endpoint: 'generate-result',
      ok: false,
      status,
      error: message,
      ip
    });
    return res.status(status).json({ success: false, error: message });
  }
});

app.post('/api/:endpoint', limiter, async (req, res, next) => {
  const timestamp = new Date().toISOString();
  const codeBody = (req.body && typeof req.body.code === 'string') ? req.body.code : '';
  const formattedLog = `{
  "timestamp": "${timestamp}",
  "endpoint": "${req.params.endpoint}",
  "body":
${codeBody.split('\n').map(line => '    ' + line).join('\n')}
  ,
  "ip": "${req.headers['x-real-ip'] || req.ip}"
}\n`;
  try {
    const logFile = path.join(LOG_DIR, `requests-${timestamp.split('T')[0]}.log`);
    await fsp.appendFile(logFile, formattedLog, 'utf8');
  } catch (error) {
    console.error('Error logging request:', error);
  }
  next();
});

app.post('/api/:endpoint', async (req, res) => {
  try {
    const endpoint = req.params.endpoint;
    if (!VALID_ENDPOINTS.includes(endpoint)) {
      return res.status(400).json({
        data: 'none',
        message: 'Invalid endpoint'
      });
    }
    const { code } = req.body;
    const response = await requestQueue.addRequest(endpoint, code);
    if (endpoint === 'stl' || endpoint === 'step') {
      const contentDisposition = response.headers && response.headers['content-disposition'];
      if (contentDisposition) {
        res.setHeader('Content-Disposition', contentDisposition);
      }
      res.setHeader('Content-Type', 'application/octet-stream');
      res.send(response);
    } else {
      res.send(response);
    }
  } catch (error) {
    const status = error.status || 500;
    res.status(status).json({
      data: 'none',
      message: error.message || 'Internal server error'
    });
  }
});

app.use(express.static(WEB_DIR, { index: 'index.html', extensions: ['html'] }));

app.listen(NODE_PORT, NODE_HOST, () => {
  console.log(`Node.js server listening on http://${NODE_HOST}:${NODE_PORT}`);
  console.log(`Serving frontend from ${WEB_DIR}`);
  console.log(`Forwarding /api/* to CadQuery server at http://${process.env.CADQUERY_HOST || '127.0.0.1'}:${process.env.CADQUERY_PORT || '5002'}`);
  console.log(`LLM generation via vLLM at ${VLLM_URL} (model: ${VLLM_MODEL})`);
});

process.on('SIGTERM', () => {
  console.log('SIGTERM signal received.');
  process.exit(0);
});
