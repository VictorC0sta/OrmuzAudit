"""
broker_setor.py — Componente Broker de Setor do sistema Ormuz Command Center.

Papel na arquitetura:
    Atua como um roteador intermediário entre o Sensor do setor e as Bases.
    Ele recebe os dados "brutos" do sensor, VERIFICA a assinatura HMAC do
    alerta (prova de que o empresa_id alegado é mesmo de quem mandou —
    ver shared/auth.py), estampa um Timestamp Lógico (Lamport) para
    garantir a ordenação dos eventos no sistema distribuído, e dispara
    essa requisição simultaneamente para todas as 4 bases.

    A partir daqui (setor -> bases -> ledger), o empresa_id já passou pela
    verificação de origem — não é assinado de novo em cada hop, porque essa
    parte da rede é infraestrutura nossa, não entrada não confiável vinda
    de fora.
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
from auth import verificar

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

PORTA_BASE_NORTE = int(os.environ.get("PORTA_BASE_NORTE", "6001"))
PORTA_BASE_SUL   = int(os.environ.get("PORTA_BASE_SUL",   "6002"))
PORTA_BASE_LESTE = int(os.environ.get("PORTA_BASE_LESTE", "6003"))
PORTA_BASE_OESTE = int(os.environ.get("PORTA_BASE_OESTE", "6004"))

PRIORIDADE = os.environ.get("PRIORIDADE", "NORTE,SUL,LESTE,OESTE")

BROADCAST_MAX_TENTATIVAS = int(os.environ.get("BROADCAST_MAX_TENTATIVAS", "3"))
BROADCAST_RETRY_DELAY_S  = float(os.environ.get("BROADCAST_RETRY_DELAY_S", "1.0"))

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

clock = LamportClock()

# ── Lógica de Rede e Tolerância a Falhas ──────────────────────────────────────

def broadcast_com_retry(payload: dict) -> dict[str, bool]:
    resultados_finais: dict[str, bool] = {}
    pendentes = list(BASES)

    for tentativa in range(1, BROADCAST_MAX_TENTATIVAS + 1):
        if not pendentes:
            break 

        parcial = tcp_broadcast(pendentes, payload)
        resultados_finais.update(parcial)

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

    empresa_id = msg.get("empresa_id")

    # ── VERIFICAÇÃO DE AUTENTICIDADE (HMAC) ──────────────────────────────
    # Antes de qualquer outra coisa: este alerta realmente vem de quem diz
    # ser? Se a assinatura não bater (empresa errada, segredo errado, ou
    # qualquer campo alterado depois de assinado), rejeita aqui mesmo — o
    # alerta nunca chega a virar uma cobrança no ledger.
    if not verificar(
        msg.get("setor_id", SETOR_ID), msg.get("tipo_ocorrencia"),
        msg.get("criticidade"), empresa_id, msg.get("id_alerta"),
        msg.get("assinatura"),
    ):
        logger.warning(
            "[%s] Alerta REJEITADO — assinatura HMAC invalida (empresa_id=%s pode estar forjado)",
            SETOR_ID, empresa_id,
        )
        notificar_monitor({
            "tipo": "PAGAMENTO_RECUSADO", "setor": SETOR_ID, "empresa": empresa_id,
            "motivo": "assinatura invalida — empresa_id nao autenticado",
            "id_requisicao": msg.get("id_alerta", ""),
        })
        return

    ts = clock.incrementar()
    custo = calcular_custo(msg.get("criticidade"))

    requisicao = MensagemRequisicao(
        id_setor=SETOR_ID,
        timestamp_logico=ts,
        criticidade=msg.get("criticidade", "BAIXA"),
        tipo_ocorrencia=msg.get("tipo_ocorrencia", "anomalia_menor"),
    )

    payload = asdict(requisicao)
    payload["empresa_id"] = empresa_id 

    logger.info(
        "[%s] Alerta autenticado → req %s | %s [%s] | empresa=%s | Lamport=%d",
        SETOR_ID, requisicao.id_requisicao[:8], requisicao.tipo_ocorrencia, requisicao.criticidade, empresa_id, ts,
    )

    # ── LOGICA DE PRÉ-FILTRAGEM COM FAIL-OPEN (DEGRADAÇÃO GRACIOSA) ──
    # Se o ledger do Setor falhar (ex: peer offline), não derrubamos a missão.
    # Repassamos para a Base, pois ela validará o saldo definitivamente antes de despachar.
    saldo_atual, status_ledger = ledger_client.consultar_saldo(empresa_id)
    
    if status_ledger == "ERRO_REDE":
        logger.warning("[%s] Ledger inacessível. Fail-open ativado: alerta encaminhado para validação definitiva pelas Bases.", SETOR_ID)
        # Segue para o broadcast sem dar return!
        
    elif status_ledger == "NAO_ENCONTRADA" or saldo_atual is None:
        motivo = "Empresa não está registrada no consórcio."
        logger.warning("[%s] Requisição %s REJEITADA — %s", SETOR_ID, requisicao.id_requisicao[:8], motivo)
        notificar_monitor({
            "tipo": "PAGAMENTO_RECUSADO", "setor": SETOR_ID, "empresa": empresa_id,
            "motivo": motivo, "id_requisicao": requisicao.id_requisicao
        })
        return
        
    elif saldo_atual < custo:
        motivo = f"Saldo insuficiente (Requer: {custo}, Atual: {saldo_atual})"
        logger.warning("[%s] Requisição %s REJEITADA — %s", SETOR_ID, requisicao.id_requisicao[:8], motivo)
        notificar_monitor({
            "tipo": "PAGAMENTO_RECUSADO", "setor": SETOR_ID, "empresa": empresa_id,
            "motivo": motivo, "id_requisicao": requisicao.id_requisicao
        })
        return
        
    else:
        logger.info("[%s] Pré-filtragem OK. Saldo atual: %d tokens. Encaminhando para as bases...", SETOR_ID, saldo_atual)

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