"""
ledger_client.py — Cliente Hyperledger Fabric para o sistema Ormuz Command Center.

Papel na arquitetura:
    Wrapper que esconde toda a complexidade do Fabric do restante do projeto.
    É o único arquivo que conhece o fabric-sdk-py — broker_setor.py e base/broker.py
    chamam apenas as funções públicas deste módulo, sem saber nada sobre peers,
    channels ou endorsement policies.

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
    CC_FN_REGISTRAR_CONCLUSAO = "RegistrarConclusao"
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

    def consultar_saldo(self, empresa_id: str) -> Optional[int]:
        if not self._reconectar_se_necessario(): return None
        try:
            resultado = self._query(self.CC_FN_SALDO, [empresa_id]) or {}
            saldo = resultado.get("saldo")
            return int(saldo) if saldo is not None else None
        except Exception as e:
            logger.error("[LedgerClient] Erro saldo | emp=%s: %s", empresa_id, e)
            self._conectado = False
            return None

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

    # ── Log de Missão e Cobrança Atômica ──────────────────────────────────────

    def registrar_conclusao(
        self, id_requisicao: str, drone_id: str, base_id: str, setor_id: str,
        tipo_ocorrencia: str, criticidade: str, empresa_id: str, custo: int
    ) -> Tuple[bool, str]:
        """
        Registra o laudo e executa a cobrança da empresa em uma única transação atômica.
        """
        if not self._reconectar_se_necessario():
            return False, "LEDGER_OFFLINE"
        try:
            timestamp = str(int(time.time()))
            
            # ATUALIZADO: A assinatura no Go agora espera idEmpresa e custoStr no final
            args = [
                id_requisicao, drone_id, base_id, setor_id, timestamp, 
                tipo_ocorrencia, str(criticidade), empresa_id, str(custo)
            ]
            
            resultado = self._invoke(self.CC_FN_REGISTRAR_CONCLUSAO, args) or {}
            sucesso = resultado.get("sucesso", False)
            motivo = resultado.get("motivo", "OK" if sucesso else "desconhecido")

            if sucesso:
                logger.info("[LedgerClient] Missão e Cobrança Registradas | req=%s drone=%s emp=%s custo=%d saldo_restante=%s",
                            id_requisicao[:8], drone_id, empresa_id, custo, resultado.get("saldo_restante", "?"))
            else:
                logger.warning("[LedgerClient] Falha ao concluir missão | req=%s motivo=%s", id_requisicao[:8], motivo)

            return sucesso, motivo
        except Exception as e:
            logger.error("[LedgerClient] Erro ao registrar conclusão | req=%s: %s", id_requisicao[:8], e)
            self._conectado = False
            return False, "ERRO_REDE"

    def auditoria_empresa(self, empresa_id: str) -> List[Dict[str, Any]]:
        if not self._reconectar_se_necessario(): return []
        try:
            # REMOVIDO o pdc_name, pois o chaincode em Go não o exige mais.
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