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
from typing import Optional

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
# Padrão: "_implicit_org_<OrgName>" — coleção implícita por organização (Fabric 2.x)
PDC_PREFIX  = os.environ.get("FABRIC_PDC_PREFIX", "_implicit_org_")


# ── Classe principal ──────────────────────────────────────────────────────────

class LedgerClient:
    """
    Cliente do Hyperledger Fabric para o sistema Ormuz.

    Responsabilidades:
        - Gerenciar a conexão com o gateway Fabric (inicialização lazy).
        - Expor operações de negócio simples para broker_setor.py e base/broker.py.
        - Separar transações de escrita (submit) de leituras (evaluate).
        - Tratar erros de rede sem propagar exceções Fabric para o restante do sistema.

    Importante:
        Esta classe NÃO contém lógica de negócio. Decisões como "rejeitar requisição
        por saldo insuficiente" ficam no broker_setor.py. Aqui só trafega dados.
    """

    def __init__(self):
        self._gateway  = None   # hfc.Gateway — inicializado em _conectar()
        self._network  = None   # channel
        self._contract = None   # chaincode handle
        self._conectado = False

    # ── Conexão ───────────────────────────────────────────────────────────────

    def _conectar(self) -> bool:
        """
        Inicializa a conexão com o gateway Fabric de forma lazy.
        Chamado automaticamente na primeira operação — não é necessário
        chamar explicitamente, mas pode ser chamado no startup para
        detectar problemas de configuração cedo.

        Retorna True se conectou com sucesso, False caso contrário.
        """
        if self._conectado:
            return True

        try:
            # fabric-sdk-py: import aqui para não quebrar o restante do projeto
            # se o SDK não estiver instalado no ambiente de desenvolvimento
            try:
                import hfc.fabric as hfc  # type: ignore
            except ImportError as ie:
                logger.error("[LedgerClient] Falha ao importar fabric-sdk-py: %s. Execute: pip install fabric-sdk-py", ie)
                return False

            # Carrega o perfil de conexão gerado pelo fabric/setup.sh
            with open(CONNECTION_PROFILE, "r", encoding="utf-8") as f:
                profile = json.load(f)

            # Instancia o cliente com o perfil de conexão
            self._gateway = hfc.Client.new_with_crypto_suite(profile)

            # Carrega as credenciais do usuário Admin da organização
            # Os certificados ficam em fabric/crypto-config/<OrgName>/users/<UserName>/
            self._gateway.new_channel(CHANNEL)
            self._network  = self._gateway.get_channel(CHANNEL)
            self._contract = self._network.get_contract(CHAINCODE)

            self._conectado = True
            logger.info("[LedgerClient] Conectado ao Fabric | org=%s user=%s channel=%s cc=%s",
                        ORG_NAME, USER_NAME, CHANNEL, CHAINCODE)
            return True

        except FileNotFoundError:
            logger.error(
                "[LedgerClient] connection-profile.json não encontrado em: %s. "
                "Execute fabric/setup.sh primeiro.", CONNECTION_PROFILE
            )
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

    # ── Operações financeiras — chamadas pelo broker_setor.py ─────────────────

    def debitar(self, empresa_id: str, valor: int, id_requisicao: str) -> bool:
        """
        Debita tokens da carteira da empresa antes do broadcast para as bases.

        O chaincode verifica o saldo internamente e rejeita a transação se for
        insuficiente — neste caso a função retorna False e o broker_setor.py
        deve descartar a requisição sem fazer broadcast.

        Args:
            empresa_id:     Identificador da empresa (ex: "EMPRESA-A").
            valor:          Quantidade de tokens a debitar (ex: 3 para CRITICA).
            id_requisicao:  UUID da requisição — usado como chave de idempotência
                            no chaincode para evitar débito duplo em retransmissões.

        Retorna:
            True  — débito realizado, broadcast pode prosseguir.
            False — saldo insuficiente ou falha de rede.
        """
        if not self._reconectar_se_necessario():
            # Se o ledger estiver offline, decidimos não bloquear o sistema.
            # Altere para 'return False' se quiser comportamento mais restritivo.
            logger.warning("[LedgerClient] Ledger offline — débito ignorado para req %s", id_requisicao[:8])
            return True

        try:
            # submit_transaction: grava no ledger (precisa de endorsement dos peers)
            resposta = self._contract.submit_transaction(
                "DebitarTokens",
                empresa_id,
                str(valor),
                id_requisicao,
            )
            resultado = json.loads(resposta) if resposta else {}
            sucesso = resultado.get("sucesso", False)

            if sucesso:
                logger.info(
                    "[LedgerClient] Débito OK | empresa=%s valor=%d req=%s saldo_restante=%s",
                    empresa_id, valor, id_requisicao[:8], resultado.get("saldo_restante", "?")
                )
            else:
                logger.warning(
                    "[LedgerClient] Débito RECUSADO | empresa=%s valor=%d req=%s motivo=%s",
                    empresa_id, valor, id_requisicao[:8], resultado.get("motivo", "saldo insuficiente")
                )

            return sucesso

        except Exception as e:
            logger.error("[LedgerClient] Erro ao debitar | empresa=%s req=%s: %s",
                         empresa_id, id_requisicao[:8], e)
            self._conectado = False  # força reconexão na próxima chamada
            return False

    def consultar_saldo(self, empresa_id: str) -> Optional[int]:
        """
        Consulta o saldo atual de tokens de uma empresa.

        Usa evaluate_transaction (só leitura — não cria bloco, mais rápido).
        Chamado pelo broker_setor.py antes do débito para log/auditoria,
        e opcionalmente pelo monitor para exibir saldo em tempo real.

        Args:
            empresa_id: Identificador da empresa.

        Retorna:
            Saldo em tokens, ou None em caso de falha.
        """
        if not self._reconectar_se_necessario():
            return None

        try:
            # evaluate_transaction: só lê o estado — não precisa de endosso
            resposta = self._contract.evaluate_transaction(
                "ConsultarSaldo",
                empresa_id,
            )
            resultado = json.loads(resposta) if resposta else {}
            saldo = resultado.get("saldo")

            logger.debug("[LedgerClient] Saldo | empresa=%s saldo=%s", empresa_id, saldo)
            return int(saldo) if saldo is not None else None

        except Exception as e:
            logger.error("[LedgerClient] Erro ao consultar saldo | empresa=%s: %s", empresa_id, e)
            self._conectado = False
            return None

    # ── Operações de auditoria — chamadas pelo base/broker.py ─────────────────

    def registrar_conclusao(
        self,
        id_requisicao: str,
        drone_id: str,
        base_id: str,
        setor_id: str,
    ) -> bool:
        """
        Registra no ledger que uma missão foi concluída com sucesso.

        Chamado pelo base/broker.py em _processar_heartbeat_tcp() após receber
        a confirmação TCP do drone. Fecha o ciclo financeiro da requisição:
        o ledger passa a ter evidência de que o serviço foi prestado.

        Args:
            id_requisicao:  UUID da requisição concluída.
            drone_id:       ID do drone que executou a missão.
            base_id:        Base que despachou o drone (ex: "NORTE").
            setor_id:       Setor atendido (ex: "S3") — gravado para auditoria.

        Retorna:
            True se registrado com sucesso, False caso contrário.
        """
        if not self._reconectar_se_necessario():
            logger.warning("[LedgerClient] Ledger offline — conclusão não registrada para req %s", id_requisicao[:8])
            return False

        try:
            timestamp = str(int(time.time()))
            resposta = self._contract.submit_transaction(
                "RegistrarConclusao",
                id_requisicao,
                drone_id,
                base_id,
                setor_id,
                timestamp,
            )
            resultado = json.loads(resposta) if resposta else {}
            sucesso = resultado.get("sucesso", False)

            if sucesso:
                logger.info(
                    "[LedgerClient] Conclusão registrada | req=%s drone=%s base=%s setor=%s",
                    id_requisicao[:8], drone_id, base_id, setor_id
                )
            else:
                logger.warning(
                    "[LedgerClient] Falha ao registrar conclusão | req=%s motivo=%s",
                    id_requisicao[:8], resultado.get("motivo", "desconhecido")
                )

            return sucesso

        except Exception as e:
            logger.error("[LedgerClient] Erro ao registrar conclusão | req=%s: %s",
                         id_requisicao[:8], e)
            self._conectado = False
            return False

    def auditoria_empresa(self, empresa_id: str) -> list[dict]:
        """
        Retorna o histórico completo de transações de uma empresa.

        Usa Private Data Collections (PDC) — só a organização dona dos dados
        consegue ler. Outras organizações recebem apenas o hash da transação.

        Chamado opcionalmente pelo monitor ou por uma rota administrativa.

        Args:
            empresa_id: Identificador da empresa.

        Retorna:
            Lista de dicts com os campos:
                id_requisicao, setor_id, criticidade, valor_debitado,
                drone_id, base_id, timestamp_debito, timestamp_conclusao, status
            Lista vazia em caso de falha ou sem transações.
        """
        if not self._reconectar_se_necessario():
            return []

        try:
            # O nome da PDC segue a convenção do collections_config.json
            pdc_name = f"{PDC_PREFIX}{ORG_NAME}"

            resposta = self._contract.evaluate_transaction(
                "AuditoriaEmpresa",
                empresa_id,
                pdc_name,
            )

            if not resposta:
                return []

            resultado = json.loads(resposta)
            transacoes = resultado.get("transacoes", [])

            logger.info(
                "[LedgerClient] Auditoria | empresa=%s transacoes=%d",
                empresa_id, len(transacoes)
            )
            return transacoes

        except Exception as e:
            logger.error("[LedgerClient] Erro na auditoria | empresa=%s: %s", empresa_id, e)
            self._conectado = False
            return []

    # ── Operações administrativas — chamadas pelo fabric/init_ledger.sh ───────

    def criar_carteira(self, empresa_id: str, saldo_inicial: int) -> bool:
        """
        Inicializa a carteira de uma empresa com saldo inicial de tokens.

        Chamado uma única vez durante o setup da rede (fabric/init_ledger.sh).
        Falha silenciosamente se a carteira já existir — o chaincode é idempotente
        nessa operação para permitir re-execução segura do script de setup.

        Args:
            empresa_id:    Identificador da empresa (ex: "EMPRESA-A").
            saldo_inicial: Tokens iniciais (ex: 100).

        Retorna:
            True se criada (ou já existia), False em caso de erro.
        """
        if not self._reconectar_se_necessario():
            return False

        try:
            resposta = self._contract.submit_transaction(
                "CriarCarteira",
                empresa_id,
                str(saldo_inicial),
            )
            resultado = json.loads(resposta) if resposta else {}
            sucesso = resultado.get("sucesso", False)

            if sucesso:
                logger.info(
                    "[LedgerClient] Carteira criada | empresa=%s saldo_inicial=%d",
                    empresa_id, saldo_inicial
                )
            else:
                logger.warning(
                    "[LedgerClient] Carteira não criada | empresa=%s motivo=%s",
                    empresa_id, resultado.get("motivo", "já existe")
                )

            return sucesso

        except Exception as e:
            logger.error("[LedgerClient] Erro ao criar carteira | empresa=%s: %s", empresa_id, e)
            self._conectado = False
            return False


# ── Singleton global ──────────────────────────────────────────────────────────
# Importar este objeto em broker_setor.py e base/broker.py:

ledger = LedgerClient()