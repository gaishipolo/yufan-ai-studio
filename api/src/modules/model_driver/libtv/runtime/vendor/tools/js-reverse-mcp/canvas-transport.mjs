import { randomUUID } from 'node:crypto';

export const API = 'https://api.liblib.tv';

export class Cdp {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    socket.addEventListener('message', event => {
      const data = JSON.parse(event.data);
      const waiting = this.pending.get(data.id);
      if (!waiting) return;
      this.pending.delete(data.id);
      clearTimeout(waiting.timer);
      if (data.error) waiting.reject(new Error(`CDP error ${data.error.code}`));
      else waiting.resolve(data.result);
    });
    socket.addEventListener('close', () => {
      for (const waiting of this.pending.values()) {
        clearTimeout(waiting.timer);
        waiting.reject(new Error('CDP disconnected'));
      }
      this.pending.clear();
    });
  }
  static async connect(endpoint = 'http://127.0.0.1:9226') {
    const url = new URL(endpoint);
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.protocol !== 'http:') throw new Error('Only a loopback CDP endpoint is allowed');
    const response = await fetch(new URL('/json/version', url), {signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error(`CDP discovery HTTP ${response.status}`);
    const metadata = await response.json();
    const socketUrl = new URL(metadata.webSocketDebuggerUrl);
    if (socketUrl.hostname !== url.hostname || socketUrl.port !== url.port || socketUrl.protocol !== 'ws:') throw new Error('Unexpected CDP WebSocket endpoint');
    const socket = new WebSocket(socketUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {socket.close(); reject(new Error('CDP connection timed out'));}, 8000);
      socket.addEventListener('open', () => {clearTimeout(timer); resolve();}, {once:true});
      socket.addEventListener('error', () => {clearTimeout(timer); reject(new Error('CDP connection failed'));}, {once:true});
    });
    return new Cdp(socket);
  }
  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {this.pending.delete(id); reject(new Error(`CDP ${method} timed out`));}, 20000);
      this.pending.set(id, {resolve, reject, timer});
      this.socket.send(JSON.stringify({id,method,params}));
    });
  }
  close() {this.socket.close();}
}

export async function loginHeaders(endpoint) {
  const cdp = await Cdp.connect(endpoint);
  try {
    const {cookies} = await cdp.send('Storage.getCookies');
    const siteCookies = cookies.filter(c => ['www.liblib.tv','.liblib.tv','liblib.tv'].includes(c.domain) && (c.expires <= 0 || c.expires > Date.now()/1000));
    const token = siteCookies.find(c => c.name === 'usertoken')?.value;
    if (!token) throw new Error('No LibTV login in the project browser. Run start-browser.ps1 and sign in.');
    return {token, webid:siteCookies.find(c=>c.name==='webid')?.value ?? '',
      'x-language':'zh', Origin:'https://www.liblib.tv', Referer:'https://www.liblib.tv/',
      'Content-Type':'application/json', 'User-Agent':'Mozilla/5.0'};
  } finally {cdp.close();}
}

export class CanvasApi {
  constructor(headers) {this.headers = headers;}
  static async fromBrowser(endpoint) {return new CanvasApi(await loginHeaders(endpoint));}
  async request(path, body) {
    if (!path.startsWith('/api/') || path.includes('?') || path.includes('..')) throw new Error('Invalid API path');
    // No automatic retries: a timeout during creation must be reconciled first.
    const response = await fetch(API+path, {
      method:body === undefined ? 'GET' : 'POST',
      headers:{...this.headers, 'X-Log-ID':randomUUID()},
      body:body === undefined ? undefined : JSON.stringify(body),
      redirect:'error', signal:AbortSignal.timeout(45000),
    });
    if (!response.ok) throw new Error(`LibTV HTTP ${response.status}`);
    const result = await response.json();
    if (result.code !== 0) throw new Error(`LibTV business code ${result.code}; no retry`);
    return result;
  }
}
