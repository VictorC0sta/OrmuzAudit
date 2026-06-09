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

import json
import logging
import os
import time
import threading
from typing import Optional, Tuple, List, Dict, Any

logger = logging.getLogger("ledger_client")

# ── Configuração via variáveis de ambiente ────────────────────────────────────

# Caminho para o arquivo gerado pelo 'fabric/setup.sh'
CONNECTION_PROFILE = os.environ.get(
    "FABRIC_CONNECTION_PROFILE",
    os.path.join(os.path.dirname(__file__), "..", "fabric", "connection-profile.json"),
)

# Organização e usuário que assina as transações neste nó
ORG_NAME    = os.environ.get("FABRIC_ORG",      "OrgNorte")   # OrgNorte/OrgSul/OrgLeste/OrgOeste
USER_NAME   = os.environ.get("FABRIC_USER",     "Admin")
CHANNEL     = os.environ.get("FABRIC_CHANNEL",  "ormuz-channel")
CHAINCODE   = os.environ.get("FABRIC_CHAINCODE", "token_contract")

# Nome da Private Data Collection definida em chaincode/collections_config.json
PDC_PREFIX  = os.environ.get("FABRIC_PDC_PREFIX", "_implicit_org_")


# ── Classe principal ──────────────────────────────────────────────────────────

class LedgerClient:
    """
    Cliente do Hyperledger Fabric para o sistema Ormuz.

    Responsabilidades:
        - Gerenciar a conexão com o gateway Fabric (inicialização lazy e thread-safe).
        - Expor operações de negócio simples para broker_setor.py e base/broker.py.
        - Separar transações de escrita (submit) de leituras (evaluate).
        - Tratar erros de rede sem propagar exceções Fabric para o restante do sistema.
    """

    # Constantes com os nomes exatos das funções no Chaincode (Solidity/Go/Node)
    CC_FN_DEBITAR = "DebitarTokens"
    CC_FN_SALDO = "ConsultarSaldo"
    CC_FN_REGISTRAR_CONCLUSAO = "RegistrarConclusao"
    CC_FN_AUDITORIA = "AuditoriaEmpresa"
    CC_FN_CRIAR_CARTEIRA = "CriarCarteira"

    def __init__(self):
        self._gateway  = None   # hfc.Gateway
        self._network  = None   # channel
        self._contract = None   # chaincode handle
        self._conectado = False
        self._lock = threading.Lock() # Garante thread-safety na inicialização lazy

    # ── Conexão ───────────────────────────────────────────────────────────────

    def _conectar(self) -> bool:
        """
        Inicializa a conexão com o gateway Fabric usando double-checked locking
        para evitar que múltiplas threads tentem conectar simultaneamente.
        """
        if self._conectado:
            return True

        with self._lock:
            # Checagem secundária após adquirir o lock
            if self._conectado:
                return True

            try:
                try:
                    import hfc.fabric as hfc  # type: ignore
                except ImportError as ie:
                    logger.error("[LedgerClient] Falha ao importar fabric-sdk-py. Execute: pip install fabric-sdk-py")
                    return False

                with open(CONNECTION_PROFILE, "r", encoding="utf-8") as f:
                    profile = json.load(f)

                self._gateway = hfc.Client.new_with_crypto_suite(profile)
                self._gateway.new_channel(CHANNEL)
                self._network  = self._gateway.get_channel(CHANNEL)
                self._contract = self._network.get_contract(CHAINCODE)

                self._conectado = True
                logger.info("[LedgerClient] Conectado ao Fabric | org=%s user=%s channel=%s cc=%s",
                            ORG_NAME, USER_NAME, CHANNEL, CHAINCODE)
                return True

            except FileNotFoundError:
                logger.error("[LedgerClient] connection-profile.json não encontrado em: %s. Execute fabric/setup.sh primeiro.", CONNECTION_PROFILE)
                return False

            except Exception as e:
                logger.error("[LedgerClient] Falha ao conectar ao Fabric: %s", e)
                return False

    def _reconectar_se_necessario(self) -> bool:
        """Tenta reconectar se a conexão anterior caiu."""
        if not self._conectado:
            self._gateway  = None
            self._network  = None
            self._contract = None
            return self._conectar()
        return True

    def desconectar(self):
        """Fecha a conexão com o Fabric graciosamente (Graceful shutdown)."""
        if self._conectado and hasattr(self, '_gateway'):
            logger.info("[LedgerClient] Desconectando do Fabric...")
            self._conectado = False

    # ── Operações financeiras — chamadas pelo broker_setor.py ─────────────────

    def debitar(self, empresa_id: str, valor: int, id_requisicao: str) -> Tuple[bool, str]:
        """
        Debita tokens da carteira da empresa antes do broadcast para as bases.

        Retorna:
            (Sucesso (bool), Motivo/Status (str))
            Ex: (True, "OK"), (False, "saldo insuficiente"), (False, "ERRO_REDE")
        """
        if not self._reconectar_se_necessario():
            logger.warning("[LedgerClient] Ledger offline — débito ignorado para req %s", id_requisicao[:8])
            return False, "LEDGER_OFFLINE"

        try:
            resposta = self._contract.submit_transaction(
                self.CC_FN_DEBITAR,
                empresa_id,
                str(valor),
                id_requisicao,
            )
            resultado = json.loads(resposta) if resposta else {}
            sucesso = resultado.get("sucesso", False)
            motivo = resultado.get("motivo", "OK" if sucesso else "saldo insuficiente")

            if sucesso:
                logger.info("[LedgerClient] Débito OK | empresa=%s valor=%d req=%s saldo_restante=%s",
                            empresa_id, valor, id_requisicao[:8], resultado.get("saldo_restante", "?"))
            else:
                logger.warning("[LedgerClient] Débito RECUSADO | empresa=%s valor=%d req=%s motivo=%s",
                               empresa_id, valor, id_requisicao[:8], motivo)

            return sucesso, motivo

        except Exception as e:
            logger.error("[LedgerClient] Erro ao debitar | empresa=%s req=%s: %s", empresa_id, id_requisicao[:8], e)
            self._conectado = False
            return False, "ERRO_REDE"

    def consultar_saldo(self, empresa_id: str) -> Optional[int]:
        """
        Consulta o saldo atual de tokens de uma empresa usando leitura rápida (evaluate).
        """
        if not self._reconectar_se_necessario():
            return None

        try:
            resposta = self._contract.evaluate_transaction(self.CC_FN_SALDO, empresa_id)
            resultado = json.loads(resposta) if resposta else {}
            saldo = resultado.get("saldo")

            logger.debug("[LedgerClient] Saldo | empresa=%s saldo=%s", empresa_id, saldo)
            return int(saldo) if saldo is not None else None

        except Exception as e:
            logger.error("[LedgerClient] Erro ao consultar saldo | empresa=%s: %s", empresa_id, e)
            self._conectado = False
            return None

    # ── Operações de auditoria — chamadas pelo base/broker.py ─────────────────

    def registrar_conclusao(self, id_requisicao: str, drone_id: str, base_id: str, setor_id: str) -> Tuple[bool, str]:
        """
        Registra no ledger que uma missão foi concluída com sucesso.

        Retorna:
            (Sucesso (bool), Motivo/Status (str))
        """
        if not self._reconectar_se_necessario():
            logger.warning("[LedgerClient] Ledger offline — conclusão não registrada para req %s", id_requisicao[:8])
            return False, "LEDGER_OFFLINE"

        try:
            timestamp = str(int(time.time()))
            resposta = self._contract.submit_transaction(
                self.CC_FN_REGISTRAR_CONCLUSAO,
                id_requisicao,
                drone_id,
                base_id,
                setor_id,
                timestamp,
            )
            resultado = json.loads(resposta) if resposta else {}
            sucesso = resultado.get("sucesso", False)
            motivo = resultado.get("motivo", "OK" if sucesso else "desconhecido")

            if sucesso:
                logger.info("[LedgerClient] Conclusão registrada | req=%s drone=%s base=%s setor=%s",
                            id_requisicao[:8], drone_id, base_id, setor_id)
            else:
                logger.warning("[LedgerClient] Falha ao registrar conclusão | req=%s motivo=%s",
                               id_requisicao[:8], motivo)

            return sucesso, motivo

        except Exception as e:
            logger.error("[LedgerClient] Erro ao registrar conclusão | req=%s: %s", id_requisicao[:8], e)
            self._conectado = False
            return False, "ERRO_REDE"

    def auditoria_empresa(self, empresa_id: str) -> List[Dict[str, Any]]:
        """
        Retorna o histórico completo de transações de uma empresa via Private Data Collections.
        """
        if not self._reconectar_se_necessario():
            return []

        try:
            pdc_name = f"{PDC_PREFIX}{ORG_NAME}"
            resposta = self._contract.evaluate_transaction(
                self.CC_FN_AUDITORIA,
                empresa_id,
                pdc_name,
            )

            if not resposta:
                return []

            resultado = json.loads(resposta)
            transacoes = resultado.get("transacoes", [])

            logger.info("[LedgerClient] Auditoria | empresa=%s transacoes=%d", empresa_id, len(transacoes))
            return transacoes

        except Exception as e:
            logger.error("[LedgerClient] Erro na auditoria | empresa=%s: %s", empresa_id, e)
            self._conectado = False
            return []

    # ── Operações administrativas — chamadas pelo fabric/init_ledger.sh ───────

    def criar_carteira(self, empresa_id: str, saldo_inicial: int) -> Tuple[bool, str]:
        """
        Inicializa a carteira de uma empresa com saldo inicial de tokens.

        Retorna:
            (Sucesso (bool), Motivo/Status (str))
        """
        if not self._reconectar_se_necessario():
            return False, "LEDGER_OFFLINE"

        try:
            resposta = self._contract.submit_transaction(
                self.CC_FN_CRIAR_CARTEIRA,
                empresa_id,
                str(saldo_inicial),
            )
            resultado = json.loads(resposta) if resposta else {}
            sucesso = resultado.get("sucesso", False)
            motivo = resultado.get("motivo", "OK" if sucesso else "já existe")

            if sucesso:
                logger.info("[LedgerClient] Carteira criada | empresa=%s saldo_inicial=%d", empresa_id, saldo_inicial)
            else:
                logger.warning("[LedgerClient] Carteira não criada | empresa=%s motivo=%s", empresa_id, motivo)

            return sucesso, motivo

        except Exception as e:
            logger.error("[LedgerClient] Erro ao criar carteira | empresa=%s: %s", empresa_id, e)
            self._conectado = False
            return False, "ERRO_REDE"


# ── Singleton global ──────────────────────────────────────────────────────────
# Importar este objeto em broker_setor.py e base/broker.py:

ledger = LedgerClient()