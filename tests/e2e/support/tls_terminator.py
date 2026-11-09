"""A TLS terminator for the joint drive: it listens with the drive's certificate and copies every connection's bytes,
unread and unchanged, to a plain-HTTP server on loopback, as the proxy in front of Skat does in production.

    python tests/e2e/support/tls_terminator.py --listen-port N --target-port N --certificate F --key F
"""

import argparse
import asyncio
import ssl

LOOPBACK = "127.0.0.1"
CHUNK_BYTES = 65536


async def _copy(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    try:
        while chunk := await source.read(CHUNK_BYTES):
            sink.write(chunk)
            await sink.drain()
    except ConnectionError:
        pass
    finally:
        sink.close()


def _connection_handler(target_port: int):
    async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        try:
            server_reader, server_writer = await asyncio.open_connection(LOOPBACK, target_port)
        except OSError:
            client_writer.close()
            return
        await asyncio.gather(_copy(client_reader, server_writer), _copy(server_reader, client_writer))

    return handle


async def serve(listen_port: int, target_port: int, certificate: str, key: str) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server = await asyncio.start_server(_connection_handler(target_port), LOOPBACK, listen_port, ssl=context)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--key", required=True)
    options = parser.parse_args()
    asyncio.run(serve(options.listen_port, options.target_port, options.certificate, options.key))
