/// @file RequestQueue.js
/// @brief Implements a queue with unique identifiers (bare-metal adaptation)
/// @author 30hours

const axios = require('axios');

const CADQUERY_HOST = process.env.CADQUERY_HOST || '127.0.0.1';
const CADQUERY_PORT = parseInt(process.env.CADQUERY_PORT || '5002', 10);
const CADQUERY_BASE_URL = `http://${CADQUERY_HOST}:${CADQUERY_PORT}`;

class RequestQueue {

  constructor() {
    this.queue = [];
    this.isProcessing = false;
    this.requestMap = new Map();
  }

  async addRequest(endpoint, code) {
    const request_id = Date.now() + '-' +
      Math.random().toString(36).substring(2, 11);
    const requestPromise = new Promise((resolve, reject) => {
      this.requestMap.set(request_id, { resolve, reject });
    });
    this.queue.push({ request_id, endpoint, code });
    this.processQueue();
    return requestPromise;
  }

  async processQueue() {
    if (this.isProcessing || this.queue.length === 0) return;
    this.isProcessing = true;
    const { request_id, endpoint, code } = this.queue.shift();
    try {
      console.log(`Processing request ${request_id} with endpoint ${endpoint}`);
      const response = await axios.post(`${CADQUERY_BASE_URL}/${endpoint}`, {
        code: code
      }, {
        responseType: (endpoint === 'stl' || endpoint === 'step') ? 'arraybuffer' : 'json',
        timeout: 60000
      });
      const resolver = this.requestMap.get(request_id);
      if (resolver) {
        resolver.resolve(response.data);
        this.requestMap.delete(request_id);
      }
    } catch (error) {
      console.log('[ERROR] ', error.response?.data?.message
        || error.response?.data || error.message);
      const resolver = this.requestMap.get(request_id);
      if (resolver) {
        resolver.reject(error.response?.data ? {
          status: error.response.status,
          ...error.response.data
        } : error);
        this.requestMap.delete(request_id);
      }
    }
    this.isProcessing = false;
    this.processQueue();
  }
}

module.exports = RequestQueue;
