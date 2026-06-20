import asyncio
import concurrent.futures
import json
import logging
import socket
import sys
import os

import websockets

# Adiciona o shared no path (necessário quando rodando fora do Docker também)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

# pylint: disable=import-error, wrong-import-position
from ledger_client import LedgerClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("monitor_bridge")

# ── Loop dedicado para chamadas ao Fabric (evita conflito de asyncio.run) ──
# O fabric-sdk-py usa asyncio.run() internamente. Rodando em uma thread
# separada com seu próprio loop, não conflita com o loop principal da bridge.
_fabric_loop = asyncio.new_event_loop()
_fabric_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="fabric"
)

def _start_fabric_loop():
    asyncio.set_event_loop(_fabric_loop)
    _fabric_loop.run_forever()

import threading
threading.Thread(target=_start_fabric_loop, daemon=True, name="fabric-loop").start()

# Uma instância de LedgerClient por thread do executor (thread-safe via Lock interno)
_ledger = LedgerClient()


def _chamar_fabric_sync(fn, *args):
    future = _fabric_executor.submit(fn, *args)
    return future.result(timeout=30)


# Servidor WebSocket

async def ws_handler(ws):
    """Gerencia um cliente WebSocket (o navegador com o index.html)."""
    addr = ws.remote_address
    logger.info("Cliente conectado: %s", addr)

    try:
        async for raw in ws:
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Mensagem malformada de %s: %r", addr, raw[:120])
                continue

            tipo = req.get("tipo")
            logger.info("Recebido do browser: tipo=%s", tipo)

            try:
                if tipo == "GET_SALDO":
                    empresa = req["empresa"]
                    saldo, status = await asyncio.get_event_loop().run_in_executor(
                        _fabric_executor, _ledger.consultar_saldo, empresa
                    )
                    await ws.send(json.dumps({
                        "tipo_resposta": "SALDO",
                        "empresa": empresa,
                        "saldo": saldo,
                        "status": status,
                    }))

                elif tipo == "GET_AUDITORIA":
                    empresa = req["empresa"]
                    txs = await asyncio.get_event_loop().run_in_executor(
                        _fabric_executor, _ledger.auditoria_empresa, empresa
                    )
                    await ws.send(json.dumps({
                        "tipo_resposta": "AUDITORIA",
                        "empresa": empresa,
                        "transacoes": txs,
                    }))

                elif tipo == "TRANSFERIR":
                    origem  = req["origem"]
                    destino = req["destino"]
                    valor   = int(req["valor"])

                    def _transferir():
                        return _ledger.transferir(origem, destino, valor)

                    sucesso, motivo = await asyncio.get_event_loop().run_in_executor(
                        _fabric_executor, _transferir
                    )
                    await ws.send(json.dumps({
                        "tipo_resposta": "TRANSFERENCIA",
                        "sucesso": sucesso,
                        "motivo": motivo,
                    }))

                else:
                    logger.warning("Tipo desconhecido recebido do browser: %s", tipo)

            except KeyError as e:
                logger.warning("Campo obrigatório ausente na requisição: %s", e)
            except Exception as e:
                logger.error("Erro ao processar requisição '%s': %s", tipo, e, exc_info=True)
                # Notifica o browser do erro sem derrubar a conexão
                try:
                    await ws.send(json.dumps({
                        "tipo_resposta": "ERRO",
                        "tipo_original": tipo,
                        "motivo": str(e),
                    }))
                except Exception:
                    pass

    except websockets.exceptions.ConnectionClosedOK:
        logger.info("Cliente desconectado normalmente: %s", addr)
    except websockets.exceptions.ConnectionClosedError as e:
        logger.warning("Conexão encerrada com erro (%s): %s", addr, e)
    except Exception as e:
        logger.error("Erro inesperado no handler de %s: %s", addr, e, exc_info=True)


#Loop UDP: recebe eventos dos brokers e repassa ao browser 

async def udp_loop(clients: set):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 8000))
    sock.setblocking(False)
    logger.info("UDP escutando na porta 8000...")

    loop = asyncio.get_running_loop()

    while True:
        try:
            data = await loop.sock_recv(sock, 65535)
            try:
                evt = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue

            mortos = set()
            for ws in set(clients):
                try:
                    await ws.send(json.dumps(evt))
                except Exception:
                    mortos.add(ws)
            clients.difference_update(mortos)

        except Exception as e:
            logger.error("Erro no loop UDP: %s", e)
            await asyncio.sleep(0.1)


async def main():
    clients: set = set()

    # Wrapper que rastreia clientes conectados
    async def _handler(ws):
        clients.add(ws)
        try:
            await ws_handler(ws)
        finally:
            clients.discard(ws)

    srv = await websockets.serve(_handler, "0.0.0.0", 8001)
    logger.info("Bridge ativa — WebSocket: ws://0.0.0.0:8001 | UDP: 8000")
    logger.info("Conectando ao Fabric: org=%s peers=%s",
                os.environ.get("FABRIC_ORG_DOMAIN", "norte.ormuz.com"),
                os.environ.get("FABRIC_PEERS", "peer0.norte.ormuz.com"))

    await asyncio.gather(
        srv.wait_closed(),
        udp_loop(clients),
    )


if __name__ == "__main__":
    asyncio.run(main())