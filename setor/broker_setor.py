"""
broker_setor.py — Componente Broker de Setor do sistema Ormuz Command Center.

Papel na arquitetura:
    Atua como um roteador intermediário entre o Sensor do setor e as Bases.
    Ele recebe os dados "brutos" do sensor, estampa um Timestamp Lógico (Lamport)
    para garantir a ordenação dos eventos no sistema distribuído, e dispara
    essa requisição simultaneamente para todas as 4 bases.
"""

import os
import sys
import time
import logging
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import json

# Adiciona pastas no path para importar módulos comuns
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

# pylint: disable=import-error, wrong-import-position
from ledger_client import ledger as ledger_client
from protocolo import notificar_monitor, criar_servidor_tcp, tcp_receber_completo, tcp_broadcast
from constantes import TipoMensagem
from mensagens import MensagemRequisicao
from lamport import LamportClock

# ── Configuração de Logging ───────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("broker_setor")

# ── Configurações via Variáveis de Ambiente ───────────────────────────────────

SETOR_ID     = os.environ.get("SETOR_ID", "S1")
SETOR_NOME   = os.environ.get("SETOR_NOME", "Norte")
MINHA_PORTA  = int(os.environ.get("MINHA_PORTA", "5001"))

# Endereços IP das 4 Bases Operacionais
IP_BASE_NORTE = os.environ.get("IP_BASE_NORTE", "127.0.0.1")
IP_BASE_SUL   = os.environ.get("IP_BASE_SUL",   "127.0.0.1")
IP_BASE_LESTE = os.environ.get("IP_BASE_LESTE", "127.0.0.1")
IP_BASE_OESTE = os.environ.get("IP_BASE_OESTE", "127.0.0.1")

# Portas TCP onde as Bases escutam requisições
PORTA_BASE_NORTE = int(os.environ.get("PORTA_BASE_NORTE", "6001"))
PORTA_BASE_SUL   = int(os.environ.get("PORTA_BASE_SUL",   "6002"))
PORTA_BASE_LESTE = int(os.environ.get("PORTA_BASE_LESTE", "6003"))
PORTA_BASE_OESTE = int(os.environ.get("PORTA_BASE_OESTE", "6004"))

PRIORIDADE = os.environ.get("PRIORIDADE", "NORTE,SUL,LESTE,OESTE")

# Configurações do mecanismo de tolerância a falhas de rede (Retry)
BROADCAST_MAX_TENTATIVAS = int(os.environ.get("BROADCAST_MAX_TENTATIVAS", "3"))
BROADCAST_RETRY_DELAY_S  = float(os.environ.get("BROADCAST_RETRY_DELAY_S", "1.0"))

# ── Destinos de broadcast (todas as 4 bases) ──────────────────────────────────

BASES: list[tuple[str, int]] = [
    (IP_BASE_NORTE, PORTA_BASE_NORTE),
    (IP_BASE_SUL,   PORTA_BASE_SUL),
    (IP_BASE_LESTE, PORTA_BASE_LESTE),
    (IP_BASE_OESTE, PORTA_BASE_OESTE),
]


_CAMINHO_CUSTO = os.path.join(os.path.dirname(__file__), "..", "config", "custo_por_criticidade.json")
with open(_CAMINHO_CUSTO, "r", encoding="utf-8") as _f:
    _CUSTO_POR_CRIT = json.load(_f)

def calcular_custo(criticidade: str) -> int:
    return _CUSTO_POR_CRIT.get(criticidade, {}).get("tokens", 1)

# ── Instâncias Globais ────────────────────────────────────────────────────────

# O Relógio de Lamport carimba cada nova requisição com um número sequencial,
# permitindo que as bases saibam qual alerta aconteceu primeiro de forma global.
clock = LamportClock()


# ── Lógica de Rede e Tolerância a Falhas ──────────────────────────────────────

def broadcast_com_retry(payload: dict) -> dict[str, bool]:
    """
    Envia a requisição para todas as bases garantindo entrega sob falhas leves.
    """
    resultados_finais: dict[str, bool] = {}
    pendentes = list(BASES)

    for tentativa in range(1, BROADCAST_MAX_TENTATIVAS + 1):
        if not pendentes:
            break 

        parcial = tcp_broadcast(pendentes, payload)
        resultados_finais.update(parcial)

        # As bases que não responderam nesta rodada são consideradas falhas temporárias.
        falhas = [(h, p) for (h, p) in pendentes if not parcial.get(f"{h}:{p}", False)]
        enviados = len(pendentes) - len(falhas)
        
        logger.info(
            "[%s] Broadcast tentativa %d/%d — %d/%d bases alcançadas%s",
            SETOR_ID, tentativa, BROADCAST_MAX_TENTATIVAS, enviados, len(pendentes),
            f" | {len(falhas)} offline, aguardando {BROADCAST_RETRY_DELAY_S}s para retry" if falhas else "",
        )

        if not falhas:
            break

        pendentes = falhas
        if tentativa < BROADCAST_MAX_TENTATIVAS:
            time.sleep(BROADCAST_RETRY_DELAY_S)

    if pendentes:
        logger.warning(
            "[%s] Bases não alcançadas após %d tentativas: %s",
            SETOR_ID, BROADCAST_MAX_TENTATIVAS, [f"{h}:{p}" for h, p in pendentes],
        )

    return resultados_finais


# ── Processamento de Dados ────────────────────────────────────────────────────

def processar_alerta(msg: dict):
    tipo = msg.get("tipo")

    if tipo != TipoMensagem.ALERTA.value:
        logger.warning("[%s] Mensagem ignorada — tipo inesperado: %s", SETOR_ID, tipo)
        return

    ts = clock.incrementar()
    empresa_id = msg.get("empresa_id")
    custo = calcular_custo(msg.get("criticidade"))

    requisicao = MensagemRequisicao(
        id_setor=SETOR_ID,
        timestamp_logico=ts,
        criticidade=msg.get("criticidade", "BAIXA"),
        tipo_ocorrencia=msg.get("tipo_ocorrencia", "anomalia_menor"),
    )

    payload = asdict(requisicao)
    # INJEÇÃO CRUCIAL: Passando o dono da carteira para frente para a cobrança futura
    payload["empresa_id"] = empresa_id 

    logger.info(
        "[%s] Alerta recebido → req %s | %s [%s] | Lamport=%d",
        SETOR_ID, requisicao.id_requisicao[:8], requisicao.tipo_ocorrencia, requisicao.criticidade, ts,
    )

    # ATENÇÃO: esta é só uma pré-filtragem OTIMISTA (sem lock, sem reserva de
    # saldo) — existe apenas para não fazer broadcast de requisições de
    # empresas obviamente sem fundos. NÃO é o ponto que garante ausência de
    # duplo gasto: como esta leitura não bloqueia o saldo, duas requisições
    # concorrentes da mesma empresa ainda podem passar por aqui ao mesmo
    # tempo. A autorização que de fato vale (débito atômico no ledger) só
    # acontece na Base, em AutorizarPagamento(), IMEDIATAMENTE ANTES do
    # despacho do drone — ver base/broker.py:_tentar_aceitar(). É lá que o
    # Fabric garante exclusão mútua real via controle de versão (MVCC) na
    # carteira, e é só depois dessa confirmação que o drone é despachado.
    saldo_atual = ledger_client.consultar_saldo(empresa_id)
    
    if saldo_atual is None:
        logger.warning("[%s] Rejeitado: Falha ao consultar o ledger ou empresa %s não existe.", SETOR_ID, empresa_id)
        return
        
    if saldo_atual < custo:
        motivo = f"Saldo insuficiente (Requer: {custo}, Atual: {saldo_atual})"
        logger.warning(
            "[%s] Requisição %s REJEITADA para empresa %s — motivo: %s",
            SETOR_ID, requisicao.id_requisicao[:8], empresa_id, motivo,
        )
        notificar_monitor({
            "tipo": "PAGAMENTO_RECUSADO",
            "setor": SETOR_ID,
            "empresa": empresa_id,
            "motivo": motivo,
            "id_requisicao": requisicao.id_requisicao,
        })
        return

    logger.info("[%s] Pré-filtragem OK. Saldo atual: %d tokens. Encaminhando para as bases — a confirmação "
                "definitiva do pagamento ocorre na base, antes do despacho do drone.", SETOR_ID, saldo_atual)
    
    broadcast_com_retry(payload)

    notificar_monitor({
        "tipo": "ALERTA_GERADO",
        "setor": SETOR_ID,
        "criticidade": requisicao.criticidade,
        "tipo_ocorrencia": requisicao.tipo_ocorrencia,
        "id_requisicao": requisicao.id_requisicao,
        "timestamp_logico": ts,
    })

# ── Servidor TCP (Recepção dos Sensores) ──────────────────────────────────────

def loop_servidor():
    servidor = criar_servidor_tcp(MINHA_PORTA)
    logger.info(
        "[%s — %s] Broker iniciado na porta %d | prioridade: %s",
        SETOR_ID, SETOR_NOME, MINHA_PORTA, PRIORIDADE,
    )

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="alerta") as pool:
        while True:
            try:
                conn, addr = servidor.accept()
                pool.submit(_tratar_conexao, conn, addr)
            except Exception as e:
                logger.error("[%s] Erro no accept: %s", SETOR_ID, e, exc_info=True)


def _tratar_conexao(conn, addr):
    try:
        msg = tcp_receber_completo(conn)
        conn.close()
        
        if msg:
            processar_alerta(msg)
        else:
            logger.warning("[%s] Conexão vazia de %s", SETOR_ID, addr)
    except Exception as e:
        logger.error("[%s] Erro ao tratar conexão de %s: %s", SETOR_ID, addr, e, exc_info=True)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info(
        "[%s] Inicializando broker | Bases: Norte=%s:%d Sul=%s:%d Leste=%s:%d Oeste=%s:%d | retry=%dx @ %.1fs",
        SETOR_ID, IP_BASE_NORTE, PORTA_BASE_NORTE, IP_BASE_SUL, PORTA_BASE_SUL,
        IP_BASE_LESTE, PORTA_BASE_LESTE, IP_BASE_OESTE, PORTA_BASE_OESTE,
        BROADCAST_MAX_TENTATIVAS, BROADCAST_RETRY_DELAY_S,
    )
    loop_servidor()

if __name__ == "__main__":
    main()