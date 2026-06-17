"""
ledger_client.py — Cliente Hyperledger Fabric para o sistema Ormuz Command Center.

Papel na arquitetura:
    Wrapper que esconde toda a complexidade do Fabric do restante do projeto.
    É o único arquivo que conhece o fabric-sdk-py — broker_setor.py e base/broker.py
    chamam apenas as funções públicas deste módulo, sem saber nada sobre peers,
    channels ou endorsement policies.

Fluxo de pagamento (ATUALIZADO):
    1. autorizar_pagamento() — chamada pela Base ANTES de despachar o drone.
       Debita a carteira da empresa no ledger. Só se isso retornar sucesso=True
       é que o drone pode ser despachado. Idempotente: requisições reemitidas
       (drone perdido) não são cobradas duas vezes.
    2. registrar_laudo() — chamada quando a missão termina. Apenas grava o
       laudo imutável; não move mais nenhum token (o pagamento já ocorreu).

Instalação:
    pip install fabric-sdk-py
"""

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ledger_client")

# ── Configuração via variáveis de ambiente ────────────────────────────────────

CONNECTION_PROFILE = os.environ.get(
    "FABRIC_CONNECTION_PROFILE",
    os.path.join(os.path.dirname(__file__), "..", "fabric", "connection-profile.json"),
)

ORG_DOMAIN  = os.environ.get("FABRIC_ORG_DOMAIN", "norte.ormuz.com")
USER_NAME   = os.environ.get("FABRIC_USER",      "Admin")
CHANNEL     = os.environ.get("FABRIC_CHANNEL",   "ormuz-channel")
CHAINCODE   = os.environ.get("FABRIC_CHAINCODE", "token_contract")
FABRIC_PEERS = os.environ.get("FABRIC_PEERS", "peer0.norte.ormuz.com").split(",")

# ── Classe principal ──────────────────────────────────────────────────────────

class LedgerClient:
    """
    Cliente do Hyperledger Fabric para o sistema Ormuz.
    """

    CC_FN_SALDO = "ConsultarSaldo"
    CC_FN_AUTORIZAR_PAGAMENTO = "AutorizarPagamento"
    CC_FN_REGISTRAR_LAUDO = "RegistrarLaudo"
    CC_FN_AUDITORIA = "AuditoriaEmpresa"
    CC_FN_CRIAR_CARTEIRA = "CriarCarteira"
    CC_FN_TRANSFERIR = "TransferirTokens"

    def __init__(self):
        self._client = None
        self._requestor = None
        self._conectado = False
        self._lock = threading.Lock()

    # ── Conexão ───────────────────────────────────────────────────────────────

    def _conectar(self) -> bool:
        if self._conectado:
            return True
        with self._lock:
            if self._conectado:
                return True
            try:
                try:
                    from hfc.fabric import Client as FabricClient  # type: ignore
                except ImportError:
                    logger.error("[LedgerClient] Falha ao importar fabric-sdk-py. Execute: pip install fabric-sdk-py")
                    return False

                if not os.path.exists(CONNECTION_PROFILE):
                    raise FileNotFoundError(CONNECTION_PROFILE)

                self._client = FabricClient(net_profile=CONNECTION_PROFILE)
                self._requestor = self._client.get_user(org_name=ORG_DOMAIN, name=USER_NAME)
                self._conectado = True
                logger.info("[LedgerClient] Conectado ao Fabric | org=%s cc=%s", ORG_DOMAIN, CHAINCODE)
                return True
            except Exception as e:
                logger.error("[LedgerClient] Falha ao conectar ao Fabric: %s", e)
                return False

    def _reconectar_se_necessario(self) -> bool:
        if not self._conectado:
            self._client = None
            self._requestor = None
            return self._conectar()
        return True

    def desconectar(self):
        if self._conectado:
            logger.info("[LedgerClient] Desconectando do Fabric...")
            self._conectado = False

    # ── Helpers internos de invoke/query ────────────────────────────────────

    def _invoke(self, fcn: str, args: List[str]) -> Optional[dict]:
        resposta = asyncio.run(self._client.chaincode_invoke(
            requestor=self._requestor, channel_name=CHANNEL, peers=FABRIC_PEERS,
            cc_name=CHAINCODE, fcn=fcn, args=args, wait_for_event=True,
        ))
        if not resposta: return None
        return json.loads(resposta) if isinstance(resposta, str) else resposta

    def _query(self, fcn: str, args: List[str]) -> Optional[dict]:
        resposta = asyncio.run(self._client.chaincode_query(
            requestor=self._requestor, channel_name=CHANNEL, peers=FABRIC_PEERS,
            cc_name=CHAINCODE, fcn=fcn, args=args,
        ))
        if not resposta: return None
        return json.loads(resposta) if isinstance(resposta, str) else resposta

    # ── Operações financeiras ─────────────────────────────────────────────────

    def consultar_saldo(self, empresa_id: str) -> Tuple[Optional[int], str]:
        """
        Retorna (saldo, status).
        Status possíveis: "OK", "NAO_ENCONTRADA", "ERRO_REDE".
        """
        if not self._reconectar_se_necessario(): 
            return None, "ERRO_REDE"
        
        try:
            resultado = self._query(self.CC_FN_SALDO, [empresa_id]) or {}
            if "saldo" in resultado:
                return int(resultado["saldo"]), "OK"
            return None, "NAO_ENCONTRADA"
            
        except Exception as e:
            erro = str(e).lower()
            # Diferencia falha real de negócio de falha técnica de rede
            if "nao existe" in erro or "not found" in erro:
                return None, "NAO_ENCONTRADA"
                
            logger.error("[LedgerClient] Erro de rede ao consultar saldo | emp=%s: %s", empresa_id, e)
            self._conectado = False
            return None, "ERRO_REDE"

    def transferir(self, origem_id: str, destino_id: str, valor: int) -> Tuple[bool, str]:
        if not self._reconectar_se_necessario(): return False, "LEDGER_OFFLINE"
        try:
            resultado = self._invoke(self.CC_FN_TRANSFERIR, [origem_id, destino_id, str(valor)]) or {}
            sucesso = resultado.get("sucesso", False)
            motivo = resultado.get("motivo", "OK" if sucesso else "falha na transferencia")
            return sucesso, motivo
        except Exception as e:
            logger.error("[LedgerClient] Erro transferencia: %s", e)
            self._conectado = False
            return False, "ERRO_REDE"

    # ── Pagamento ANTES do despacho + Laudo imutável DEPOIS da missão ──────────

    def autorizar_pagamento(self, id_requisicao: str, empresa_id: str, custo: int) -> Tuple[bool, str]:
        if not self._reconectar_se_necessario():
            return False, "LEDGER_OFFLINE"
        try:
            resultado = self._invoke(self.CC_FN_AUTORIZAR_PAGAMENTO, [id_requisicao, empresa_id, str(custo)]) or {}
            sucesso = resultado.get("sucesso", False)
            motivo = resultado.get("motivo", "OK" if sucesso else "desconhecido")

            if sucesso:
                logger.info("[LedgerClient] Pagamento autorizado | req=%s emp=%s custo=%d saldo_restante=%s",
                            id_requisicao[:8], empresa_id, custo, resultado.get("saldo_restante", "?"))
            else:
                logger.warning("[LedgerClient] Pagamento NEGADO | req=%s emp=%s custo=%d motivo=%s",
                               id_requisicao[:8], empresa_id, custo, motivo)

            return sucesso, motivo
        except Exception as e:
            logger.error("[LedgerClient] Erro ao autorizar pagamento | req=%s: %s", id_requisicao[:8], e)
            self._conectado = False
            return False, "ERRO_REDE"

    def registrar_laudo(
        self, id_requisicao: str, drone_id: str, base_id: str, setor_id: str,
        tipo_ocorrencia: str, criticidade: str
    ) -> Tuple[bool, str]:
        if not self._reconectar_se_necessario():
            return False, "LEDGER_OFFLINE"
        try:
            timestamp = str(int(time.time()))
            args = [id_requisicao, drone_id, base_id, setor_id, timestamp, tipo_ocorrencia, str(criticidade)]

            resultado = self._invoke(self.CC_FN_REGISTRAR_LAUDO, args) or {}
            sucesso = resultado.get("sucesso", False)
            motivo = resultado.get("motivo", "OK" if sucesso else "desconhecido")

            if sucesso:
                logger.info("[LedgerClient] Laudo registrado | req=%s drone=%s", id_requisicao[:8], drone_id)
            else:
                logger.warning("[LedgerClient] Falha ao registrar laudo | req=%s motivo=%s", id_requisicao[:8], motivo)

            return sucesso, motivo
        except Exception as e:
            logger.error("[LedgerClient] Erro ao registrar laudo | req=%s: %s", id_requisicao[:8], e)
            self._conectado = False
            return False, "ERRO_REDE"

    def auditoria_empresa(self, empresa_id: str) -> List[Dict[str, Any]]:
        if not self._reconectar_se_necessario(): return []
        try:
            resultado = self._query(self.CC_FN_AUDITORIA, [empresa_id]) or {}
            return resultado.get("transacoes", [])
        except Exception as e:
            logger.error("[LedgerClient] Erro auditoria | emp=%s: %s", empresa_id, e)
            self._conectado = False
            return []

    def criar_carteira(self, empresa_id: str, saldo_inicial: int) -> Tuple[bool, str]:
        if not self._reconectar_se_necessario(): return False, "LEDGER_OFFLINE"
        try:
            resultado = self._invoke(self.CC_FN_CRIAR_CARTEIRA, [empresa_id, str(saldo_inicial)]) or {}
            return resultado.get("sucesso", False), resultado.get("motivo", "OK")
        except Exception as e:
            self._conectado = False
            return False, "ERRO_REDE"

ledger = LedgerClient()