"""Second-screen relay: Unreal publishes the live match on ws://<host>:8765/ue; every other connection is a viewer and gets
each message as it comes (and the latest pose at once, so a new viewer sees the athletes before the next frame).

    python Web/live.py [--host 0.0.0.0] [--port 8765]
    python Web/serve.py --host 0.0.0.0                       (the page, for phones on the same network)

Unreal: MENU > VIEW > Second screen (or -SlapLive=ws://127.0.0.1:8765/ue). Viewers: http://<host>:8080/Web/?live=ws://<host>:8765
Off the local network, point both at an Azure Web PubSub hub instead (client access URLs; Unreal sends to the group
"live", the page joins it) and this relay isn't needed. Stdlib only: RFC 6455 text frames, no extensions.
"""
import argparse, asyncio, base64, hashlib, struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
viewers = set()
latest = {"pose": None}


async def handshake(reader, writer):
    head = (await reader.readuntil(b"\r\n\r\n")).decode("latin1").split("\r\n")
    hdr = {k.strip().lower(): v.strip() for k, v in (h.split(":", 1) for h in head[1:] if ":" in h)}
    accept = base64.b64encode(hashlib.sha1((hdr["sec-websocket-key"] + GUID).encode()).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    await writer.drain()
    return head[0].split(" ")[1]   # the path


async def message(reader):
    """One whole message: (opcode, payload), fragments joined, client masks removed."""
    op, data = None, b""
    while True:
        b0, b1 = await reader.readexactly(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", await reader.readexactly(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", await reader.readexactly(8))[0]
        mask = await reader.readexactly(4) if b1 & 0x80 else bytes(4)
        part = bytes(c ^ mask[i % 4] for i, c in enumerate(await reader.readexactly(n)))
        if b0 & 0x0F >= 8:   # a control frame may arrive between fragments
            return b0 & 0x0F, part
        op = op if op is not None else b0 & 0x0F
        data += part
        if b0 & 0x80:
            return op, data


def frame(data, op=1):
    n = len(data)
    size = bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n)
    return bytes([0x80 | op]) + size + data


async def client(reader, writer):
    try:
        path = await handshake(reader, writer)
    except (asyncio.IncompleteReadError, KeyError, IndexError, ConnectionError):
        writer.close()
        return
    publisher = path.split("?")[0].rstrip("/").endswith("/ue")
    print(f"{'unreal' if publisher else 'viewer'} connected ({len(viewers) + (not publisher)} viewers)", flush=True)
    if not publisher:
        viewers.add(writer)
        if latest["pose"]:
            writer.write(frame(latest["pose"]))
    try:
        while True:
            op, data = await message(reader)
            if op == 8:   # close
                break
            if op == 9:   # ping
                writer.write(frame(data, 10))
            elif publisher and op == 1:
                if data.startswith(b'{"t":"pose"'):
                    latest["pose"] = data
                for v in list(viewers):
                    if v.transport.get_write_buffer_size() > 1 << 20:   # a viewer this far behind is gone
                        viewers.discard(v)
                        v.close()
                    else:
                        v.write(frame(data))
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        viewers.discard(writer)
        writer.close()
        print(f"{'unreal' if publisher else 'viewer'} left ({len(viewers)} viewers)", flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 lets phones on the network watch")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    server = await asyncio.start_server(client, a.host, a.port)
    print(f"PoSlap second screen: Unreal -> ws://{a.host}:{a.port}/ue, viewers -> ws://{a.host}:{a.port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
