"""
auth.py — Autenticação dos alertas dos sensores (Ormuz Command Center).

Papel na arquitetura:
    Resolve o ponto em que "empresa_id" viajava como texto solto, sem
    nenhuma prova de quem realmente o enviou. A fronteira de confiança do
    sistema é o Sensor: é o único componente que recebe entrada do "mundo
    real" (a detecção da anomalia). Tudo daqui pra dentro (setor -> bases ->
    ledger) é infraestrutura nossa.

    Cada empresa tem um segredo compartilhado (config/chaves_empresas.json).
    O Sensor assina o alerta com HMAC-SHA256 usando o segredo da empresa
    dele. O Broker de Setor verifica essa assinatura ANTES de aceitar o
    alerta — se a assinatura não bater (empresa errada, segredo errado, ou
    qualquer campo do payload alterado depois de assinado), o alerta é
    rejeitado e nunca chega a virar uma cobrança no ledger.

    Não é PKI/assinatura digital de chave pública (isso exigiria mudar o
    chaincode em Go para validar a identidade lá dentro) — é autenticação
    simétrica, suficiente para provar que "quem mandou isso conhece o
    segredo daquela empresa", o que já fecha a brecha de spoofing de
    empresa_id em trânsito.
"""

import hmac
import hashlib
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("auth")

_CAMINHO_CHAVES = os.path.join(os.path.dirname(__file__), "..", "config", "chaves_empresas.json")

try:
    with open(_CAMINHO_CHAVES, "r", encoding="utf-8") as _f:
        _CHAVES: dict[str, str] = json.load(_f)
except FileNotFoundError:
    logger.error("[auth] Arquivo de chaves nao encontrado em %s — nenhuma assinatura sera aceita.", _CAMINHO_CHAVES)
    _CHAVES = {}


def _mensagem_canonica(setor_id: str, tipo_ocorrencia: str, criticidade: str, empresa_id: str, id_alerta: str) -> str:
    """
    Monta a string exata que é assinada/verificada. Os campos são unidos com
    "|" e na MESMA ordem dos dois lados (sensor e broker) — se a ordem ou o
    conteúdo de qualquer campo mudar depois de assinado, a verificação falha.
    """
    return f"{setor_id}|{tipo_ocorrencia}|{criticidade}|{empresa_id}|{id_alerta}"


def assinar(setor_id: str, tipo_ocorrencia: str, criticidade: str, empresa_id: str, id_alerta: str) -> str:
    """
    Usado pelo Sensor. Gera a assinatura HMAC-SHA256 do alerta usando o
    segredo da empresa dele. Lança KeyError se a empresa não tiver segredo
    cadastrado (falha alto e rápido — não deixa o sensor mandar alerta sem
    conseguir se autenticar).
    """
    segredo = _CHAVES[empresa_id]
    msg = _mensagem_canonica(setor_id, tipo_ocorrencia, criticidade, empresa_id, id_alerta)
    return hmac.new(segredo.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def verificar(
    setor_id: Optional[str],
    tipo_ocorrencia: Optional[str],
    criticidade: Optional[str],
    empresa_id: Optional[str],
    id_alerta: Optional[str],
    assinatura: Optional[str],
) -> bool:
    """
    Usado pelo Broker de Setor. Recalcula o HMAC esperado com o segredo da
    empresa alegada e compara com a assinatura recebida usando
    hmac.compare_digest (comparação em tempo constante, evita timing attack).
    Retorna False — nunca lança exceção — para qualquer entrada malformada,
    empresa desconhecida ou assinatura ausente/errada.
    """
    if not empresa_id or not assinatura or not id_alerta:
        return False

    segredo = _CHAVES.get(empresa_id)
    if not segredo:
        logger.warning("[auth] empresa_id desconhecido: %s", empresa_id)
        return False

    esperado = hmac.new(
        segredo.encode("utf-8"),
        _mensagem_canonica(setor_id or "", tipo_ocorrencia or "", criticidade or "", empresa_id, id_alerta).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(esperado, assinatura)