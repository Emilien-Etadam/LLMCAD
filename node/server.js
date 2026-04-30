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
const { CADAgent, VLLM_URL, VLLM_MODEL } = require('./llm');

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

app.post('/api/agent', limiter, async (req, res) => {
  const body = req.body || {};
  const prompt = typeof body.prompt === 'string' ? body.prompt : '';
  if (!prompt) {
    return res.status(400).json({ error: 'prompt manquant' });
  }

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  const maxIterations = parseInt(process.env.AGENT_MAX_ITERATIONS || '5', 10);
  const requestTimeout = parseInt(process.env.AGENT_REQUEST_TIMEOUT_MS || '30000', 10);
  const cadServerUrl = process.env.CAD_SERVER_URL || `http://${process.env.CADQUERY_HOST || '127.0.0.1'}:${process.env.CADQUERY_PORT || '5002'}`;

  const agent = new CADAgent({
    vllmBaseUrl: process.env.VLLM_BASE_URL || process.env.VLLM_URL,
    model: process.env.VLLM_MODEL,
    cadServerUrl,
    maxIterations,
    requestTimeout
  });

  const writeSSE = (event) => {
    res.write(`data: ${JSON.stringify(event)}\n\n`);
  };

  try {
    for await (const event of agent.run(prompt)) {
      writeSSE(event);
    }
  } catch (err) {
    const status = String(err && err.status || '');
    const isTimeout = err && (err.name === 'AbortError' || status === '408' || status === '504');
    writeSSE({
      type: 'fatal_error',
      error: err && err.message ? err.message : String(err),
      reason: isTimeout ? 'timeout' : 'internal_error'
    });
  } finally {
    res.end();
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
