"""
broker.py — Broker de Base (Ormuz Command Center)

Papel na arquitetura:
    É o cérebro descentralizado das bases operacionais (Norte, Sul, Leste, Oeste).
    Responsável por manter uma cópia local da Fila Replicada, gerenciar a frota 
    de drones locais e executar o algoritmo de exclusão mútua distribuída 
    (baseado em timeout por prioridade) para decidir quem atende cada ocorrência.
"""

import os
import sys
import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor

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

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("base")

# ── Configuração via ambiente ─────────────────────────────────────────────────
BASE_ID         = os.environ.get("BASE_ID", "NORTE")
MINHA_PORTA     = int(os.environ.get("MINHA_PORTA", "6001"))      # Escuta comandos TCP
MINHA_PORTA_UDP = int(os.environ.get("MINHA_PORTA_UDP", "6101"))  # Escuta heartbeats UDP
IP_PC_DRONES    = os.environ.get("IP_PC_DRONES", "127.0.0.1")

# Mapeamento de todas as bases para o broadcast TCP
TODAS_AS_BASES = {
    "NORTE": (os.environ.get("IP_BASE_NORTE", "127.0.0.1"), int(os.environ.get("PORTA_BASE_NORTE", "6001"))),
    "SUL":   (os.environ.get("IP_BASE_SUL",   "127.0.0.1"), int(os.environ.get("PORTA_BASE_SUL",   "6002"))),
    "LESTE": (os.environ.get("IP_BASE_LESTE", "127.0.0.1"), int(os.environ.get("PORTA_BASE_LESTE", "6003"))),
    "OESTE": (os.environ.get("IP_BASE_OESTE", "127.0.0.1"), int(os.environ.get("PORTA_BASE_OESTE", "6004"))),
}

# Destinos do broadcast de ACEITE (lista de tuplas IP/Porta, excluindo a própria base)
OUTRAS_BASES = [
    (host, porta)
    for base_id, (host, porta) in TODAS_AS_BASES.items()
    if base_id != BASE_ID
]

# Tempo máximo sem receber pacote UDP antes de considerar o drone abatido/perdido
HEARTBEAT_TIMEOUT_S = float(
    os.environ.get("HEARTBEAT_TIMEOUT", str(HEARTBEAT_INTERVALO_S * (HEARTBEAT_MAX_FALHAS + 1)))
)

# ── Instâncias Globais ────────────────────────────────────────────────────────
clock      = LamportClock()                   # Relógio lógico para ordenação global de eventos
estado     = FilaReplicada()                  # Estrutura de dados thread-safe da fila
prioridade = GerenciadorPrioridade(BASE_ID)   # Define o tempo de espera (timeout) desta base

# Controle de timers para o mecanismo de exclusão mútua distribuída
_timers: dict[str, threading.Timer] = {}
_timers_lock = threading.Lock()


# ── Algoritmo de Exclusão Mútua Distribuída ───────────────────────────────────

def _tentar_aceitar(id_requisicao: str) -> None:
    """
    Função engatilhada pelo Timer de prioridade.
    Tenta garantir atomicamente a posse da requisição caso nenhuma base
    mais prioritária tenha aceitado a missão durante o período de espera.
    """
    # Remove o timer do controle de pendências
    with _timers_lock:
        _timers.pop(id_requisicao, None)

    # Verifica se a ocorrência já foi capturada por outra base
    status = estado.status_requisicao(id_requisicao)
    if status != StatusRequisicao.PENDENTE.value:
        logger.debug("[%s] Req %s já está '%s'.", BASE_ID, id_requisicao[:8], status)
        return

    # Procura um recurso ocioso local
    drone = estado.drone_livre()
    if drone is None:
        logger.info("[%s] Sem drone livre para req %s — aguardando.", BASE_ID, id_requisicao[:8])
        return

    # Tenta cravar a requisição localmente. O lock interno previne race conditions (ex: duas threads locais).
    if not estado.marcar_aceita(id_requisicao):
        return

    # Aloca formalmente o drone para a missão
    estado.ocupar_drone(drone.drone_id, id_requisicao)
    logger.info("[%s] Aceitando req %s -> drone %s", BASE_ID, id_requisicao[:8], drone.drone_id)

    # Passo crucial: Avisa a malha que pegou a missão, forçando as outras bases a cancelarem seus timers.
    aceite = {
        "tipo": TipoMensagem.ACEITE.value,
        "id_requisicao": id_requisicao,
        "base_id": BASE_ID,
        "drone_id": drone.drone_id,
        "timestamp_logico": clock.incrementar(),
    }
    tcp_broadcast(OUTRAS_BASES, aceite)
    
    # Envia os dados táticos para o hardware do drone
    _despachar_drone(drone, id_requisicao)


def _despachar_drone(drone, id_requisicao: str) -> None:
    """Envia o comando de missão fisicamente via TCP para o contêiner do Drone."""
    entrada = estado.obter_entrada(id_requisicao)
    if not entrada:
        return

    missao = {
        "tipo": "MISSAO",
        "id_requisicao": entrada.id_requisicao,
        "setor_id": entrada.id_setor,
        "timestamp_logico": entrada.timestamp_logico,
        "criticidade": entrada.criticidade,
        "tipo_ocorrencia": entrada.tipo_ocorrencia,
        "base_origem": BASE_ID,
    }

    if tcp_enviar(IP_PC_DRONES, drone.porta_tcp, missao):
        logger.info("[%s] Drone %s despachado para req %s", BASE_ID, drone.drone_id, id_requisicao[:8])
    else:
        # Se a porta do drone recusar conexão, assume falha imediata do hardware
        logger.warning("[%s] Falha ao contatar drone %s.", BASE_ID, drone.drone_id)
        _tratar_drone_perdido(drone.drone_id)

    notificar_monitor({
        "tipo": "DRONE_DESPACHADO",
        "base": BASE_ID,
        "drone": drone.drone_id,
        "setor": entrada.id_setor,
    })


# ── Roteadores de Mensagens (Handlers TCP) ────────────────────────────────────

def _processar_requisicao(msg: dict) -> None:
    """
    Recebeu alerta do Setor.
    1. Sincroniza o relógio de Lamport.
    2. Registra na fila.
    3. Dá start no cronômetro (Timer) correspondente ao seu nível de prioridade.
    """
    id_req   = msg.get("id_requisicao", "")
    id_setor = msg.get("id_setor", "")
    ts       = msg.get("timestamp_logico", 0)

    # Filtro de idempotência: ignora requisições duplicadas por broadcast
    if not estado.verificar_e_registrar_vista(id_req):
        return

    clock.atualizar(ts)

    entrada = EntradaFila(
        id_requisicao=id_req,
        id_setor=id_setor,
        timestamp_logico=ts,
        criticidade=msg.get("criticidade", Criticidade.BAIXA.value),
        tipo_ocorrencia=msg.get("tipo_ocorrencia", ""),
    )
    estado.inserir_na_fila(entrada)

    timeout_s = prioridade.timeout_para_setor(id_setor)
    logger.info(
        "[%s] Req %s recebida | setor %s | Lamport=%d | timeout=%.0fms",
        BASE_ID, id_req[:8], id_setor, ts, timeout_s * 1000
    )

    timer = threading.Timer(timeout_s, _tentar_aceitar, args=(id_req,))
    with _timers_lock:
        _timers[id_req] = timer
    timer.start()


def _processar_aceite(msg: dict) -> None:
    """
    Outra base pegou a missão.
    Deve abortar a concorrência local, matando o Timer associado à requisição.
    """
    id_req = msg.get("id_requisicao", "")
    ts     = msg.get("timestamp_logico", 0)
    clock.atualizar(ts)
    estado.marcar_aceita(id_req)

    with _timers_lock:
        timer = _timers.pop(id_req, None)
    if timer:
        timer.cancel()


def _processar_registro(msg: dict) -> None:
    """Um drone acabou de ligar/conectar e está pronto para receber ordens."""
    drone_id  = msg.get("drone_id", "")
    porta_tcp = int(msg.get("porta", 7001))
    estado.registrar_drone(drone_id, porta_tcp)
    logger.info("[%s] Drone %s registrado (porta TCP %d).", BASE_ID, drone_id, porta_tcp)
    
    # Como ganhou um recurso novo, varre a fila para ver se tem missão encavalada
    threading.Thread(target=_processar_fila_pendente, daemon=True).start()


def _processar_heartbeat_tcp(msg: dict) -> None:
    """
    Recebe a confirmação explícita via TCP de que o drone retornou à base.
    Marca a missão no histórico como CONCLUIDA.
    """
    drone_id         = msg.get("drone_id", "")
    estado_drone     = msg.get("estado", EstadoDrone.LIVRE.value)
    missao_concluida = msg.get("missao_concluida")

    estado.atualizar_estado_drone(drone_id, estado_drone)

    if missao_concluida:
        estado.marcar_concluida(missao_concluida)
        logger.info("[%s] Missão %s concluída pelo drone %s.", BASE_ID, missao_concluida[:8], drone_id)

        # ── NOVO: registrar conclusão no ledger ──
        entrada = estado.obter_entrada(missao_concluida)
        if entrada:
            ledger_client.registrar_conclusao(
                id_requisicao=missao_concluida,
                drone_id=drone_id,
                base_id=BASE_ID,
                setor_id=entrada.id_setor,
            )

        # Como o drone agora está livre, tenta puxar alguma ocorrência da fila de espera
        threading.Thread(target=_processar_fila_pendente, daemon=True).start()

        notificar_monitor({
            "tipo": "MISSAO_CONCLUIDA",
            "base": BASE_ID,
            "drone": drone_id,
            "setor_concluido": missao_concluida,
        })


def _processar_reemissao(msg: dict) -> None:
    """
    Handler de Tolerância a Falhas.
    Outra base detectou que o drone dela caiu e devolveu a missão para a rede.
    A base local reativa a requisição e agenda um novo Timer.
    """
    id_req   = msg.get("id_requisicao", "")
    id_setor = msg.get("id_setor", "")
    clock.atualizar(msg.get("timestamp_logico_base", 0))

    # Permite que a requisição seja processada novamente
    estado.remover_vista(id_req)
    entrada = estado.obter_entrada(id_req)
    
    if entrada:
        with estado.fila_lock:
            entrada.status = StatusRequisicao.PENDENTE.value
    else:
        # Recuperação de estado caso a base nem tivesse a requisição original
        nova = EntradaFila(
            id_requisicao=id_req,
            id_setor=id_setor,
            timestamp_logico=msg.get("timestamp_logico", 0),
            criticidade=msg.get("criticidade", Criticidade.BAIXA.value),
            tipo_ocorrencia=msg.get("tipo_ocorrencia", ""),
        )
        estado.inserir_na_fila(nova)

    # Dispara novamente a corrida de Exclusão Mútua
    timeout_s = prioridade.timeout_para_setor(id_setor)
    timer = threading.Timer(timeout_s, _tentar_aceitar, args=(id_req,))
    with _timers_lock:
        _timers[id_req] = timer
    timer.start()


# ── Mecanismos de Reavaliação ─────────────────────────────────────────────────

def _processar_fila_pendente() -> None:
    """
    Varre as requisições ociosas na memória (status PENDENTE) sem timers ativos.
    Isto garante o Encaminhamento Passivo: missões que as bases não puderam
    atender por falta de drones não são perdidas, sendo capturadas assim que
    a malha liberar recursos.
    """
    pendentes = estado.obter_pendentes()
    for entrada in pendentes:
        with _timers_lock:
            # Se a requisição já está competindo (tem timer), ignora
            if entrada.id_requisicao in _timers:
                continue
        _tentar_aceitar(entrada.id_requisicao)


# ── Detectores de Anomalias na Frota ──────────────────────────────────────────

def _tratar_drone_perdido(drone_id: str) -> None:
    """
    Ação disparada quando um drone fica mudo.
    Se ele carregava uma missão, o sistema executa o replanejamento automático
    fazendo o broadcast de REEMISSAO para todas as bases devolverem a
    ocorrência para a mesa de negociação.
    """
    with estado.drones_lock:
        info = estado.drones.get(drone_id)
        if not info or info.estado == EstadoDrone.PERDIDO.value:
            return
        id_req_em_curso = info.id_requisicao_atual
        info.estado = EstadoDrone.PERDIDO.value
        info.id_requisicao_atual = None

    logger.warning("[%s] Drone %s marcado como PERDIDO.", BASE_ID, drone_id)

    if not id_req_em_curso:
        return

    entrada = estado.obter_entrada(id_req_em_curso)
    if entrada:
        with estado.fila_lock:
            entrada.status = StatusRequisicao.PENDENTE.value

        reemissao = {
            "tipo": TipoMensagem.REEMISSAO.value,
            "id_requisicao": entrada.id_requisicao,
            "id_setor": entrada.id_setor,
            "timestamp_logico": entrada.timestamp_logico,
            "criticidade": entrada.criticidade,
            "tipo_ocorrencia": entrada.tipo_ocorrencia,
            "timestamp_logico_base": clock.incrementar(),
        }
        tcp_broadcast(OUTRAS_BASES, reemissao)

        # A própria base tentará reassumir a missão caso tenha outro drone disponível
        timeout_s = prioridade.timeout_para_setor(entrada.id_setor)
        timer = threading.Timer(timeout_s, _tentar_aceitar, args=(entrada.id_requisicao,))
        with _timers_lock:
            _timers[entrada.id_requisicao] = timer
        timer.start()


def _monitor_heartbeat() -> None:
    """
    Vigia contínuo da frota. Roda em background analisando o timestamp de
    cada drone registrado. Se a defasagem superar o HEARTBEAT_TIMEOUT_S,
    aciona a rotina de drone perdido.
    """
    while True:
        time.sleep(HEARTBEAT_INTERVALO_S)
        agora = time.time()
        with estado.drones_lock:
            snapshot = list(estado.drones.values())

        for info in snapshot:
            if info.estado == EstadoDrone.PERDIDO.value:
                continue
            if (agora - info.ultimo_heartbeat) > HEARTBEAT_TIMEOUT_S:
                _tratar_drone_perdido(info.drone_id)


def _loop_udp() -> None:
    """
    Recepção contínua (fire-and-forget) dos pacotes UDP emitidos pelos drones.
    O principal objetivo é atualizar o carimbo temporal de `ultimo_heartbeat`.
    """
    servidor = criar_servidor_udp(MINHA_PORTA_UDP)
    while True:
        try:
            dados, _ = servidor.recvfrom(BUFFER_UDP)
            import json
            try:
                msg = json.loads(dados.decode("utf-8"))
            except Exception:
                continue

            drone_id     = msg.get("drone_id", "")
            estado_drone = msg.get("estado", EstadoDrone.LIVRE.value)
            porta        = int(msg.get("porta", 7001))

            with estado.drones_lock:
                info = estado.drones.get(drone_id)
                if info:
                    info.estado           = estado_drone
                    info.ultimo_heartbeat = time.time()
                    info.falhas_heartbeat = 0
                else:
                    # Auto-registro em caso de drone reiniciar sozinho
                    estado.registrar_drone(drone_id, porta, estado_drone)

        except Exception as e:
            logger.error("[%s] Erro UDP: %s", BASE_ID, e)


# ── Núcleo TCP ────────────────────────────────────────────────────────────────

def _despachar_mensagem(msg: dict) -> None:
    """Roteamento simples do dicionário JSON para o handler apropriado."""
    rotas = {
        TipoMensagem.REQUISICAO.value: _processar_requisicao,
        TipoMensagem.ACEITE.value:     _processar_aceite,
        TipoMensagem.REGISTRO.value:   _processar_registro,
        TipoMensagem.HEARTBEAT.value:  _processar_heartbeat_tcp,
        TipoMensagem.REEMISSAO.value:  _processar_reemissao,
    }
    handler = rotas.get(msg.get("tipo", ""))
    if handler:
        handler(msg)
    else:
        logger.warning("[%s] Tipo de mensagem desconhecido: %s", BASE_ID, msg.get("tipo"))


def _tratar_conexao(conn, addr) -> None:
    """Isola a leitura do socket TCP, garantindo que o pool de threads não fique bloqueado."""
    try:
        msg = tcp_receber_completo(conn)
        conn.close()
        if msg:
            _despachar_mensagem(msg)
    except Exception as e:
        logger.error("[%s] Erro na conexão de %s: %s", BASE_ID, addr, e)


def main() -> None:
    """Levanta as threads de telemetria UDP e entra no loop principal do servidor TCP."""
    logger.info("[%s] Broker base iniciado. TCP=%d UDP=%d", BASE_ID, MINHA_PORTA, MINHA_PORTA_UDP)
    
    # Threads auxiliares em background
    threading.Thread(target=_loop_udp,          daemon=True, name="udp-hb").start()
    threading.Thread(target=_monitor_heartbeat, daemon=True, name="mon-hb").start()

    # O servidor TCP roda sobre um Pool para processar vários pacotes paralelos
    servidor = criar_servidor_tcp(MINHA_PORTA)
    with ThreadPoolExecutor(max_workers=16, thread_name_prefix="base") as pool:
        while True:
            try:
                conn, addr = servidor.accept()
                pool.submit(_tratar_conexao, conn, addr)
            except Exception as e:
                logger.error("Erro accept: %s", e)

if __name__ == "__main__":
    main()