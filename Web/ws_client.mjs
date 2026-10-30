// A minimal RFC 6455 client for the checks. Node 20 has no global WebSocket (it arrived in Node 22) and the
// relay speaks the bare protocol - text frames, no extensions - so the harnesses do not need a dependency.
// Usage: const ws = connect("ws://127.0.0.1:8765/ue?key=..."); ws.onopen/onmessage/onerror/onclose, ws.send(text).
import net from "node:net";
import crypto from "node:crypto";

const GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

export function connect(url) {
  const u = new URL(url);
  const key = crypto.randomBytes(16).toString("base64");
  const sock = net.connect({ host: u.hostname, port: Number(u.port || 80) });
  const ws = {
    onopen: null, onmessage: null, onerror: null, onclose: null,
    send(text) {
      try {
        const data = Buffer.from(text, "utf8");
        const mask = crypto.randomBytes(4);
        const out = Buffer.alloc(data.length);
        for (let i = 0; i < data.length; i++) out[i] = data[i] ^ mask[i % 4];
        let head;
        if (data.length < 126) head = Buffer.from([0x81, 0x80 | data.length]);
        else if (data.length < 65536) { head = Buffer.alloc(4); head[0] = 0x81; head[1] = 0xFE; head.writeUInt16BE(data.length, 2); }
        else { head = Buffer.alloc(10); head[0] = 0x81; head[1] = 0xFF; head.writeBigUInt64BE(BigInt(data.length), 2); }
        sock.write(Buffer.concat([head, mask, out]));
      } catch { /* the socket is gone: the browser API throws here too */ }
    },
    close() { try { sock.end(); } catch { } },
  };
  let buf = Buffer.alloc(0), open = false;
  sock.on("connect", () => sock.write(
    `GET ${u.pathname}${u.search} HTTP/1.1\r\nHost: ${u.host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n` +
    `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`));
  sock.on("data", chunk => {
    buf = Buffer.concat([buf, chunk]);
    if (!open) {
      const i = buf.indexOf("\r\n\r\n");
      if (i < 0) return;
      const head = buf.slice(0, i).toString("latin1");
      buf = buf.slice(i + 4);
      const accept = crypto.createHash("sha1").update(key + GUID).digest("base64");
      if (!/^HTTP\/1\.1 101/.test(head) || !head.includes(accept)) {
        if (ws.onerror) ws.onerror(new Error(`the handshake failed: ${head.split("\r\n")[0]}`));
        sock.destroy();
        return;
      }
      open = true;
      if (ws.onopen) ws.onopen();
    }
    for (;;) {
      if (buf.length < 2) return;
      const opcode = buf[0] & 0x0f;
      let len = buf[1] & 0x7f, off = 2;
      if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
      else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
      if (buf.length < off + len) return;
      const payload = buf.slice(off, off + len);
      buf = buf.slice(off + len);
      if (opcode === 1) { if (ws.onmessage) ws.onmessage({ data: payload.toString("utf8") }); }
      else if (opcode === 8) { if (ws.onclose) ws.onclose(); sock.end(); return; }
      else if (opcode === 9) sock.write(Buffer.concat([Buffer.from([0x8a, payload.length]), payload]));   // pong
    }
  });
  sock.on("error", err => { if (ws.onerror) ws.onerror(err); });
  sock.on("close", () => { if (!open) { if (ws.onerror) ws.onerror(new Error("closed before the handshake")); } else if (ws.onclose) ws.onclose(); });
  return ws;
}
