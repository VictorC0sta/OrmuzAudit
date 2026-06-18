"""
broker.py — Broker de Base (Ormuz Command Center)
"""

import os
import sys
import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

# pylint: disable=import-error, wrong-import-position
from ledger_client import ledger as ledger_client
from protocolo import notificar_monitor
from constantes import (
    TipoMensagem, EstadoDrone, StatusRequisicao, Criticidade,
    HEARTBEAT_INTERVALO_S, HEARTBEAT_MAX_FALHAS
)
from protocolo import (
    criar_servidor_tcp, criar_servidor_udp,
    tcp_receber_completo, tcp_broadcast, tcp_enviar, BUFFER_UDP,
)
from lamport import LamportClock
from fila_replicada import FilaReplicada, EntradaFila
from prioridade import GerenciadorPrioridade

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("base")

BASE_ID         = os.environ.get("BASE_ID", "NORTE")
MINHA_PORTA     = int(os.environ.get("MINHA_PORTA", "6001"))
MINHA_PORTA_UDP = int(os.environ.get("MINHA_PORTA_UDP", "6101"))
IP_PC_DRONES    = os.environ.get("IP_PC_DRONES", "127.0.0.1")

TODAS_AS_BASES = {
    "NORTE": (os.environ.get("IP_BASE_NORTE", "127.0.0.1"), int(os.environ.get("PORTA_BASE_NORTE", "6001"))),
    "SUL":   (os.environ.get("IP_BASE_SUL",   "127.0.0.1"), int(os.environ.get("PORTA_BASE_SUL",   "6002"))),
    "LESTE": (os.environ.get("IP_BASE_LESTE", "127.0.0.1"), int(os.environ.get("PORTA_BASE_LESTE", "6003"))),
    "OESTE": (os.environ.get("IP_BASE_OESTE", "127.0.0.1"), int(os.environ.get("PORTA_BASE_OESTE", "6004"))),
}

OUTRAS_BASES = [(host, porta) for base_id, (host, porta) in TODAS_AS_BASES.items() if base_id != BASE_ID]
HEARTBEAT_TIMEOUT_S = float(os.environ.get("HEARTBEAT_TIMEOUT", str(HEARTBEAT_INTERVALO_S * (HEARTBEAT_MAX_FALHAS + 1))))

_CAMINHO_CUSTO = os.path.join(os.path.dirname(__file__), "..", "config", "custo_por_criticidade.json")
with open(_CAMINHO_CUSTO, "r", encoding="utf-8") as _f:
    _CUSTO_POR_CRIT = json.load(_f)

def calcular_custo(criticidade: str) -> int:
    return _CUSTO_POR_CRIT.get(criticidade, {}).get("tokens", 1)


clock      = LamportClock()
estado     = FilaReplicada()
prioridade = GerenciadorPrioridade(BASE_ID)
_timers: dict[str, threading.Timer] = {}
_timers_lock = threading.Lock()

def _tentar_aceitar(id_requisicao: str) -> None:
    with _timers_lock: _timers.pop(id_requisicao, None)
    if estado.status_requisicao(id_requisicao) != StatusRequisicao.PENDENTE.value: return
    drone = estado.drone_livre()
    if drone is None: return

    entrada = estado.obter_entrada(id_requisicao)
    if not entrada: return
    empresa_id = getattr(entrada, "empresa_id", "DESCONHECIDA")
    custo = calcular_custo(str(entrada.criticidade))

    # Exclusão mútua local: reserva a requisição para esta base antes de
    # gastar tempo de rede com o ledger (mesma garantia de antes, via Lock).
    if not estado.marcar_aceita(id_requisicao): return

    # ── PAGAMENTO ANTES DO DESPACHO ──────────────────────────────────────
    # O drone só é ocupado/despachado se o ledger confirmar o débito agora.
    # Isso fecha a brecha em que o serviço era prestado e o pagamento só
    # era checado/feito na conclusão da missão (tarde demais para evitar
    # que uma empresa sem saldo já tivesse consumido um drone).
   # ── PAGAMENTO E PREVENÇÃO DE DUPLO DESPACHO ──────────────────────────
    pago, motivo, transacao_inedita = ledger_client.autorizar_pagamento(id_requisicao, empresa_id, custo)
    
    if not pago:
        logger.warning(
            "[%s] Pagamento NEGADO para req %s (empresa %s, custo %d): %s — missao cancelada",
            BASE_ID, id_requisicao[:8], empresa_id, custo, motivo,
        )
        with estado.fila_lock:
            entrada.status = StatusRequisicao.REJEITADA.value
        notificar_monitor({
            "tipo": "PAGAMENTO_RECUSADO", "base": BASE_ID, "empresa": empresa_id,
            "id_requisicao": id_requisicao, "motivo": motivo,
        })
        return

    is_reemissao = getattr(entrada, "is_reemissao", False)

    if not transacao_inedita and not is_reemissao:
        logger.info("[%s] Corrida P2P detectada! O Ledger avisou que a missão %s já foi paga por outra base. Abortando despacho local.", BASE_ID, id_requisicao[:8])
        with estado.fila_lock:
            entrada.status = StatusRequisicao.PENDENTE.value
        return

    estado.ocupar_drone(drone.drone_id, id_requisicao)
    logger.info(
        "[%s] Pagamento confirmado no ledger (%d tokens, empresa %s) -> aceitando req %s com drone %s",
        BASE_ID, custo, empresa_id, id_requisicao[:8], drone.drone_id,
    )

    aceite = {
        "tipo": TipoMensagem.ACEITE.value, "id_requisicao": id_requisicao,
        "base_id": BASE_ID, "drone_id": drone.drone_id, "timestamp_logico": clock.incrementar(),
    }
    tcp_broadcast(OUTRAS_BASES, aceite)
    _despachar_drone(drone, id_requisicao)

def _despachar_drone(drone, id_requisicao: str) -> None:
    entrada = estado.obter_entrada(id_requisicao)
    if not entrada: return
    missao = {
        "tipo": "MISSAO", "id_requisicao": entrada.id_requisicao, "setor_id": entrada.id_setor,
        "timestamp_logico": entrada.timestamp_logico, "criticidade": entrada.criticidade,
        "tipo_ocorrencia": entrada.tipo_ocorrencia, "base_origem": BASE_ID,
    }
    if tcp_enviar(IP_PC_DRONES, drone.porta_tcp, missao):
        logger.info("[%s] Drone %s despachado para req %s", BASE_ID, drone.drone_id, id_requisicao[:8])
    else:
        logger.warning("[%s] Falha ao contatar drone %s.", BASE_ID, drone.drone_id)
        _tratar_drone_perdido(drone.drone_id)
    notificar_monitor({"tipo": "DRONE_DESPACHADO", "base": BASE_ID, "drone": drone.drone_id, "setor": entrada.id_setor})

def _processar_requisicao(msg: dict) -> None:
    id_req, id_setor, ts = msg.get("id_requisicao", ""), msg.get("id_setor", ""), msg.get("timestamp_logico", 0)
    empresa_id = msg.get("empresa_id", "DESCONHECIDA")  # Recebendo a empresa do broker de setor
    
    if not estado.verificar_e_registrar_vista(id_req): return
    clock.atualizar(ts)

    entrada = EntradaFila(
        id_requisicao=id_req, id_setor=id_setor, timestamp_logico=ts,
        criticidade=msg.get("criticidade", Criticidade.BAIXA.value),
        tipo_ocorrencia=msg.get("tipo_ocorrencia", ""),
    )
    setattr(entrada, "empresa_id", empresa_id)

    estado.inserir_na_fila(entrada)
    timeout_s = prioridade.timeout_para_setor(id_setor)
    logger.info("[%s] Req %s | emp %s | timeout=%.0fms", BASE_ID, id_req[:8], empresa_id, timeout_s * 1000)

    timer = threading.Timer(timeout_s, _tentar_aceitar, args=(id_req,))
    with _timers_lock: _timers[id_req] = timer
    timer.start()

def _processar_aceite(msg: dict) -> None:
    id_req, ts = msg.get("id_requisicao", ""), msg.get("timestamp_logico", 0)
    clock.atualizar(ts)
    estado.marcar_aceita(id_req)
    with _timers_lock:
        if timer := _timers.pop(id_req, None): timer.cancel()

def _processar_registro(msg: dict) -> None:
    drone_id, porta_tcp = msg.get("drone_id", ""), int(msg.get("porta", 7001))
    estado.registrar_drone(drone_id, porta_tcp)
    logger.info("[%s] Drone %s registrado", BASE_ID, drone_id)
    threading.Thread(target=_processar_fila_pendente, daemon=True).start()

def _processar_heartbeat_tcp(msg: dict) -> None:
    drone_id, estado_drone = msg.get("drone_id", ""), msg.get("estado", EstadoDrone.LIVRE.value)
    missao_concluida = msg.get("missao_concluida")
    estado.atualizar_estado_drone(drone_id, estado_drone)

    if missao_concluida:
        estado.marcar_concluida(missao_concluida)
        entrada = estado.obter_entrada(missao_concluida)

        if entrada:
            empresa_id = getattr(entrada, 'empresa_id', "DESCONHECIDA")

            # O pagamento JÁ foi feito em autorizar_pagamento(), antes do
            # despacho. Aqui só registramos o laudo imutável da missão —
            # nenhum token é movido nesta etapa.
            sucesso, motivo = ledger_client.registrar_laudo(
                id_requisicao=missao_concluida,
                drone_id=drone_id,
                base_id=BASE_ID,
                setor_id=entrada.id_setor,
                tipo_ocorrencia=entrada.tipo_ocorrencia,
                criticidade=str(entrada.criticidade),
            )

            if sucesso:
                logger.info("[%s] Laudo da missao %s (drone %s, empresa %s) registrado no ledger.",
                            BASE_ID, missao_concluida[:8], drone_id, empresa_id)
            else:
                logger.warning("[%s] Falha ao registrar laudo da missao %s: %s",
                               BASE_ID, missao_concluida[:8], motivo)

        threading.Thread(target=_processar_fila_pendente, daemon=True).start()
        notificar_monitor({"tipo": "MISSAO_CONCLUIDA", "base": BASE_ID, "drone": drone_id, "setor_concluido": missao_concluida})

def _processar_reemissao(msg: dict) -> None:
    id_req, id_setor = msg.get("id_requisicao", ""), msg.get("id_setor", "")
    clock.atualizar(msg.get("timestamp_logico_base", 0))
    estado.remover_vista(id_req)
    
    entrada = estado.obter_entrada(id_req)
    if entrada:
        with estado.fila_lock: 
            entrada.status = StatusRequisicao.PENDENTE.value
        setattr(entrada, "is_reemissao", True)
    else:
        nova = EntradaFila(
            id_requisicao=id_req, 
            id_setor=id_setor, 
            timestamp_logico=msg.get("timestamp_logico", 0),
            criticidade=msg.get("criticidade", Criticidade.BAIXA.value), 
            tipo_ocorrencia=msg.get("tipo_ocorrencia", ""),
        )
        setattr(nova, "empresa_id", msg.get("empresa_id", "DESCONHECIDA"))
        setattr(nova, "is_reemissao", True)
        estado.inserir_na_fila(nova)

    # OBS: se esta requisição já tinha pagamento autorizado (caso comum de
    # reemissão por drone perdido), autorizar_pagamento() na nova tentativa
    # de _tentar_aceitar() é idempotente no chaincode e NÃO cobra de novo.
    timeout_s = prioridade.timeout_para_setor(id_setor)
    timer = threading.Timer(timeout_s, _tentar_aceitar, args=(id_req,))
    with _timers_lock: 
        _timers[id_req] = timer
    timer.start()

def _processar_fila_pendente() -> None:
    for entrada in estado.obter_pendentes():
        with _timers_lock:
            if entrada.id_requisicao in _timers: continue
        _tentar_aceitar(entrada.id_requisicao)

def _tratar_drone_perdido(drone_id: str) -> None:
    with estado.drones_lock:
        info = estado.drones.get(drone_id)
        if not info or info.estado == EstadoDrone.PERDIDO.value: return
        id_req_em_curso = info.id_requisicao_atual
        info.estado = EstadoDrone.PERDIDO.value
        info.id_requisicao_atual = None

    if not id_req_em_curso: return
    entrada = estado.obter_entrada(id_req_em_curso)
    if entrada:
        # O pagamento desta requisição já foi feito no ledger (autorizar_pagamento
        # ocorreu antes do despacho). A reemissão abaixo NÃO vai cobrar de novo —
        # ela só busca um novo drone para terminar uma missão já paga.
        with estado.fila_lock: entrada.status = StatusRequisicao.PENDENTE.value
        setattr(entrada, "is_reemissao", True)  
        reemissao = {
            "tipo": TipoMensagem.REEMISSAO.value, "id_requisicao": entrada.id_requisicao,
         "id_requisicao": entrada.id_requisicao,
            "id_setor": entrada.id_setor, "timestamp_logico": entrada.timestamp_logico,
            "criticidade": entrada.criticidade, "tipo_ocorrencia": entrada.tipo_ocorrencia,
            "timestamp_logico_base": clock.incrementar(), "empresa_id": getattr(entrada, "empresa_id", "")
        }
        tcp_broadcast(OUTRAS_BASES, reemissao)
        timeout_s = prioridade.timeout_para_setor(entrada.id_setor)
        timer = threading.Timer(timeout_s, _tentar_aceitar, args=(entrada.id_requisicao,))
        with _timers_lock: _timers[entrada.id_requisicao] = timer
        timer.start()

def _monitor_heartbeat() -> None:
    while True:
        time.sleep(HEARTBEAT_INTERVALO_S)
        agora = time.time()
        with estado.drones_lock: snapshot = list(estado.drones.values())
        for info in snapshot:
            if info.estado == EstadoDrone.PERDIDO.value: continue
            if (agora - info.ultimo_heartbeat) > HEARTBEAT_TIMEOUT_S: _tratar_drone_perdido(info.drone_id)

def _loop_udp() -> None:
    servidor = criar_servidor_udp(MINHA_PORTA_UDP)
    while True:
        try:
            dados, _ = servidor.recvfrom(BUFFER_UDP)
            try: msg = json.loads(dados.decode("utf-8"))
            except Exception: continue
            drone_id, estado_drone, porta = msg.get("drone_id", ""), msg.get("estado", EstadoDrone.LIVRE.value), int(msg.get("porta", 7001))
            with estado.drones_lock:
                if info := estado.drones.get(drone_id):
                    info.estado, info.ultimo_heartbeat, info.falhas_heartbeat = estado_drone, time.time(), 0
                else: estado.registrar_drone(drone_id, porta, estado_drone)
        except Exception as e: logger.error("[%s] Erro UDP: %s", BASE_ID, e)

def _despachar_mensagem(msg: dict) -> None:
    rotas = {
        TipoMensagem.REQUISICAO.value: _processar_requisicao, TipoMensagem.ACEITE.value: _processar_aceite,
        TipoMensagem.REGISTRO.value: _processar_registro, TipoMensagem.HEARTBEAT.value: _processar_heartbeat_tcp,
        TipoMensagem.REEMISSAO.value: _processar_reemissao,
    }
    if handler := rotas.get(msg.get("tipo", "")): handler(msg)

def _tratar_conexao(conn, addr) -> None:
    try:
        msg = tcp_receber_completo(conn)
        conn.close()
        if msg: _despachar_mensagem(msg)
    except Exception as e: logger.error("[%s] Erro na conexão: %s", BASE_ID, e)

def main() -> None:
    threading.Thread(target=_loop_udp, daemon=True, name="udp-hb").start()
    threading.Thread(target=_monitor_heartbeat, daemon=True, name="mon-hb").start()
    servidor = criar_servidor_tcp(MINHA_PORTA)
    with ThreadPoolExecutor(max_workers=16, thread_name_prefix="base") as pool:
        while True:
            try:
                conn, addr = servidor.accept()
                pool.submit(_tratar_conexao, conn, addr)
            except Exception: pass

if __name__ == "__main__": main()