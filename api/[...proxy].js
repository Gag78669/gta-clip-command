const http = require('http');
const https = require('https');

module.exports = (req, res) => {
  const origin = process.env.API_ORIGIN;
  if (!origin) {
    res.statusCode = 503;
    res.end('Set API_ORIGIN to the public Render API URL.');
    return;
  }
  const parsedOrigin = new URL(origin);
  const incoming = new URL(req.url, 'http://vercel.local');
  const backendPath = incoming.searchParams.get('path') || incoming.pathname.replace(/^\/api\/proxy/, '') || '/';
  incoming.searchParams.delete('path');
  const query = incoming.searchParams.toString();
  const target = new URL(backendPath + (query ? '?' + query : ''), parsedOrigin);
  const transport = target.protocol === 'https:' ? https : http;
  const headers = { ...req.headers, host: target.host, 'x-forwarded-proto': 'https' };
  delete headers.connection;
  delete headers['accept-encoding'];
  const upstream = transport.request(target, { method: req.method, headers }, (upstreamResponse) => {
    res.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
    upstreamResponse.pipe(res);
  });
  upstream.on('error', () => {
    if (!res.headersSent) res.statusCode = 502;
    res.end('Backend is temporarily unavailable.');
  });
  req.pipe(upstream);
};

module.exports.config = { api: { bodyParser: false } };
