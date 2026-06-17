import asyncio
import websockets
import socket
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))
# pylint: disable=import-error, wrong-import-position
from ledger_client import ledger

async def main():
    clients: set = set()

    # UDP — porta 8000 (fire-and-forget vindo dos brokers via notificar_monitor())
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 8000))
    sock.setblocking(False)

    async def ws_handler(ws):
        """Gerencia um cliente WebSocket (navegador). Recebe comandos e repassa
        ao Fabric real. Funciona com websockets 10, 11, 12 e 13."""
        clients.add(ws)
        try:
            async for raw in ws:
                try:
                    req = json.loads(raw)
                    tipo = req.get("tipo")

                    if tipo == "GET_SALDO":
                        saldo, status = await asyncio.to_thread(
                            ledger.consultar_saldo, req["empresa"]
                        )
                        await ws.send(json.dumps({
                            "tipo_resposta": "SALDO",
                            "empresa": req["empresa"],
                            "saldo": saldo,
                            "status": status
                        }))

                    elif tipo == "GET_AUDITORIA":
                        txs = await asyncio.to_thread(
                            ledger.auditoria_empresa, req["empresa"]
                        )
                        await ws.send(json.dumps({
                            "tipo_resposta": "AUDITORIA",
                            "empresa": req["empresa"],
                            "transacoes": txs
                        }))

                    elif tipo == "TRANSFERIR":
                        sucesso, motivo = await asyncio.to_thread(
                            ledger.transferir,
                            req["origem"],
                            req["destino"],
                            int(req["valor"])
                        )
                        await ws.send(json.dumps({
                            "tipo_resposta": "TRANSFERENCIA",
                            "sucesso": sucesso,
                            "motivo": motivo
                        }))

                    else:
                        print(f"[Bridge] Tipo de mensagem desconhecido: {tipo}")

                except (KeyError, json.JSONDecodeError) as e:
                    print(f"[Bridge] Mensagem malformada: {e}")
                except Exception as e:
                    print(f"[Bridge] Erro ao chamar o Fabric: {e}")
        except Exception:
            pass  # WebSocket fechou — o finally abaixo limpa
        finally:
            clients.discard(ws)

    async def udp_loop():
        """Recebe pacotes UDP dos brokers e repassa para todos os navegadores."""
        # get_running_loop() é o correto a partir do Python 3.10+
        loop = asyncio.get_running_loop()
        print("[Bridge] Aguardando pacotes UDP na porta 8000...")
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
                print(f"[Bridge] Erro no loop UDP: {e}")
                await asyncio.sleep(0.1)  # evita spin infinito em caso de erro persistente

    srv = await websockets.serve(ws_handler, "0.0.0.0", 8001)
    print("Bridge ativa: UDP:8000 → WebSocket:8001 | API Fabric bidirecional pronta")

    await asyncio.gather(srv.wait_closed(), udp_loop())


if __name__ == "__main__":
    asyncio.run(main())