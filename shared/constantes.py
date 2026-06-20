"""
constantes.py — Enums e valores fixos compartilhados por todos os módulos.
"""

from enum import Enum


class Criticidade(str, Enum):
    CRITICA = "CRITICA"
    ALTA    = "ALTA"
    BAIXA   = "BAIXA"

    def peso(self) -> int:
        pesos = {
            Criticidade.CRITICA: 3,
            Criticidade.ALTA:    2,
            Criticidade.BAIXA:   1,
        }
        return pesos[self]


class TipoOcorrencia(str, Enum):
    EMBARCACAO_DERIVA        = "embarcacao_deriva"
    BLOQUEIO_ROTA            = "bloqueio_rota"
    FALHA_SINALIZACAO        = "falha_sinalizacao"
    OBJETO_NAO_IDENTIFICADO  = "objeto_nao_identificado"
    EMBARCACAO_PERIGO        = "embarcacao_perigo"
    ANOMALIA_MENOR           = "anomalia_menor"


CRITICIDADE_POR_TIPO: dict[TipoOcorrencia, Criticidade] = {
    TipoOcorrencia.EMBARCACAO_PERIGO:       Criticidade.CRITICA,
    TipoOcorrencia.BLOQUEIO_ROTA:           Criticidade.ALTA,
    TipoOcorrencia.OBJETO_NAO_IDENTIFICADO: Criticidade.ALTA,
    TipoOcorrencia.EMBARCACAO_DERIVA:       Criticidade.BAIXA,
    TipoOcorrencia.FALHA_SINALIZACAO:       Criticidade.BAIXA,
    TipoOcorrencia.ANOMALIA_MENOR:          Criticidade.BAIXA,
}


class EstadoDrone(str, Enum):
    LIVRE    = "LIVRE"
    OCUPADO  = "OCUPADO"
    PERDIDO  = "PERDIDO"


class StatusRequisicao(str, Enum):
    PENDENTE  = "pendente"
    ACEITA    = "aceita"
    CONCLUIDA = "concluida"
    # NOVO: requisição que chegou a vencer a janela de prioridade local mas
    # foi recusada pelo ledger por falta de saldo da empresa (AutorizarPagamento
    # retornou sucesso=False). Estado terminal — não volta a ser tentada.
    REJEITADA = "rejeitada"


class TipoMensagem(str, Enum):
    ALERTA       = "ALERTA"
    REQUISICAO   = "REQUISICAO"
    ACEITE       = "ACEITE"
    HEARTBEAT    = "HEARTBEAT"
    REGISTRO     = "REGISTRO"
    REEMISSAO    = "REEMISSAO"


TIMEOUT_PRIORIDADE_MS = {1: 0, 2: 200, 3: 400, 4: 600}

HEARTBEAT_INTERVALO_S  = 3
HEARTBEAT_MAX_FALHAS   = 3