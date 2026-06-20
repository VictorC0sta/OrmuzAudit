"""
ledger_client.py — Cliente Hyperledger Fabric para o sistema Ormuz Command Center.
"""

import asyncio
import glob
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# --- PATCH GLOBAL PARA O FABRIC-SDK-PY EM PYTHON 3.9+ ---
_orig_get_event_loop = asyncio.get_event_loop
def _safe_get_event_loop():
    try:
        return _orig_get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop
asyncio.get_event_loop = _safe_get_event_loop
# --------------------------------------------------------

logger = logging.getLogger("ledger_client")

# ── Configuração via variáveis de ambiente ────────────────────────────────────
CONNECTION_PROFILE = os.environ.get(
    "FABRIC_CONNECTION_PROFILE",
    os.path.join(os.path.dirname(__file__), "..", "fabric", "connection-profile.json"),
)

ORG_DOMAIN   = os.environ.get("FABRIC_ORG_DOMAIN",  "OrgNorte")
USER_NAME    = os.environ.get("FABRIC_USER",         "Admin")
CHANNEL      = os.environ.get("FABRIC_CHANNEL",      "ormuz-channel")
CHAINCODE    = os.environ.get("FABRIC_CHAINCODE",    "token_contract")
FABRIC_PEERS = os.environ.get("FABRIC_PEERS", "peer0.norte.ormuz.com").split(",")

_ORG_DOMAIN_MAP = {
    "OrgNorte": "norte.ormuz.com",
    "OrgSul":   "sul.ormuz.com",
    "OrgLeste": "leste.ormuz.com",
    "OrgOeste": "oeste.ormuz.com",
    "norte.ormuz.com": "norte.ormuz.com",
    "sul.ormuz.com":   "sul.ormuz.com",
    "leste.ormuz.com": "leste.ormuz.com",
    "oeste.ormuz.com": "oeste.ormuz.com",
}

def _resolve_domain(org_name: str) -> str:
    return _ORG_DOMAIN_MAP.get(org_name, org_name)

def _get_first_file_path(directory: str) -> str:
    """AGORA RETORNA APENAS O CAMINHO DO ARQUIVO, NÃO O CONTEÚDO."""
    files = glob.glob(os.path.join(directory, "*"))
    if not files:
        raise FileNotFoundError(f"Nenhum arquivo encontrado em: {directory}")
    return files[0]

def _run_coroutine(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError("Não pode rodar em loop ativo")
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)

# ── Classe principal ──────────────────────────────────────────────────────────
class LedgerClient:
    CC_FN_SALDO               = "ConsultarSaldo"
    CC_FN_AUTORIZAR_PAGAMENTO = "AutorizarPagamento"
    CC_FN_REGISTRAR_LAUDO     = "RegistrarLaudo"
    CC_FN_AUDITORIA           = "AuditoriaEmpresa"
    CC_FN_CRIAR_CARTEIRA      = "CriarCarteira"
    CC_FN_TRANSFERIR          = "TransferirTokens"

    def __init__(self):
        self._client     = None
        self._requestor  = None
        self._conectado  = False
        self._lock       = threading.Lock()

    def _conectar(self) -> bool:
        if self._conectado:
            return True
        with self._lock:
            if self._conectado:
                return True
            try:
                try:
                    from hfc.fabric import Client as FabricClient
                except ImportError:
                    logger.error("[LedgerClient] fabric-sdk-py não instalado.")
                    return False

                if not os.path.exists(CONNECTION_PROFILE):
                    raise FileNotFoundError(f"Perfil não encontrado: {CONNECTION_PROFILE}")

                self._client = FabricClient(net_profile=CONNECTION_PROFILE)

                domain = _resolve_domain(ORG_DOMAIN)
                with open(CONNECTION_PROFILE) as f:
                    profile = json.load(f)

                org_info = profile.get("organizations", {}).get(ORG_DOMAIN, {})
                crypto_base = org_info.get(
                    "cryptoPath",
                    f"/app/fabric/crypto-config/peerOrganizations/{domain}",
                )

                msp_path = os.path.join(crypto_base, "users", f"Admin@{domain}", "msp")
                
                # Buscando os caminhos que a biblioteca exigiu no log
                cert_path = _get_first_file_path(os.path.join(msp_path, "signcerts"))
                key_path  = _get_first_file_path(os.path.join(msp_path, "keystore"))
                
                mspid = org_info.get("mspid", f"{ORG_DOMAIN}MSP")

                self._requestor = self._client.get_user(org_name=ORG_DOMAIN, name=USER_NAME)

                if self._requestor is None:
                    logger.info(
                        "[LedgerClient] state_store vazio. Injetando Admin via create_user() | "
                        "org=%s mspid=%s", ORG_DOMAIN, mspid
                    )
                    
                    from hfc.fabric.user import create_user
                    
                    # Chamada puramente posicional! Sem kwargs, sem margem para erro de nomes.
                    self._requestor = create_user(
                        USER_NAME,                 # 1. name
                        ORG_DOMAIN,                # 2. org
                        self._client.state_store,  # 3. state_store
                        mspid,                     # 4. msp_id
                        key_path,                  # 5. key_path
                        cert_path                  # 6. cert_path
                    )

                # --- A CORREÇÃO DE OURO ENTRA AQUI ---
                # Forçamos a biblioteca a criar o objeto do canal em memória
                if self._client.get_channel(CHANNEL) is None:
                    logger.info("[LedgerClient] Instanciando o canal em memória: %s", CHANNEL)
                    self._client.new_channel(CHANNEL)
                # -------------------------------------

                self._conectado = True
                logger.info(
                    "[LedgerClient] Conectado com Sucesso! | org=%s mspid=%s",
                    ORG_DOMAIN, mspid
                )
                return True

            except Exception as e:
                logger.error("[LedgerClient] Falha Crítica ao conectar: %s", e)
                return False

    def _reconectar_se_necessario(self) -> bool:
        if not self._conectado:
            self._client    = None
            self._requestor = None
            return self._conectar()
        return True

    def desconectar(self):
        self._conectado = False

    def _invoke(self, fcn: str, args: List[str]) -> Optional[dict]:
        coro = self._client.chaincode_invoke(
            requestor=self._requestor,
            channel_name=CHANNEL,
            peers=FABRIC_PEERS,
            cc_name=CHAINCODE,
            fcn=fcn,
            args=args,
            wait_for_event=True,
        )
        resposta = _run_coroutine(coro)
        if not resposta: return None
        return json.loads(resposta) if isinstance(resposta, str) else resposta

    def _query(self, fcn: str, args: List[str]) -> Optional[dict]:
        coro = self._client.chaincode_query(
            requestor=self._requestor,
            channel_name=CHANNEL,
            peers=FABRIC_PEERS,
            cc_name=CHAINCODE,
            fcn=fcn,
            args=args,
        )
        resposta = _run_coroutine(coro)
        if not resposta: return None
        return json.loads(resposta) if isinstance(resposta, str) else resposta

    def consultar_saldo(self, empresa_id: str) -> Tuple[Optional[int], str]:
        if not self._reconectar_se_necessario(): return None, "ERRO_REDE"
        try:
            resultado = self._query(self.CC_FN_SALDO, [empresa_id]) or {}
            if "saldo" in resultado: return int(resultado["saldo"]), "OK"
            return None, "NAO_ENCONTRADA"
        except Exception as e:
            logger.error("[LedgerClient] ERRO TRANSAÇÃO (consultar_saldo): %s", e)
            self._conectado = False
            return None, "ERRO_REDE"

    def transferir(self, origem_id: str, destino_id: str, valor: int) -> Tuple[bool, str]:
        if not self._reconectar_se_necessario(): return False, "LEDGER_OFFLINE"
        try:
            resultado = self._invoke(self.CC_FN_TRANSFERIR, [origem_id, destino_id, str(valor)]) or {}
            sucesso = resultado.get("sucesso", False)
            motivo  = resultado.get("motivo", "OK" if sucesso else "falha na transferencia")
            return sucesso, motivo
        except Exception as e:
            logger.error("[LedgerClient] ERRO TRANSAÇÃO (transferir): %s", e)
            self._conectado = False
            return False, "ERRO_REDE"

    def autorizar_pagamento(self, id_requisicao: str, empresa_id: str, custo: int) -> Tuple[bool, str, bool, Optional[int]]:
        if not self._reconectar_se_necessario(): return False, "LEDGER_OFFLINE", False, None
        try:
            resultado = self._invoke(self.CC_FN_AUTORIZAR_PAGAMENTO, [id_requisicao, empresa_id, str(custo)]) or {}
            return (
                resultado.get("sucesso", False),
                resultado.get("motivo", "OK" if resultado.get("sucesso") else "desconhecido"),
                resultado.get("transacao_inedita", False),
                resultado.get("saldo_restante")
            )
        except Exception as e:
            logger.error("[LedgerClient] ERRO TRANSAÇÃO (autorizar_pagamento): %s", e)
            self._conectado = False
            return False, "ERRO_REDE", False, None

    def registrar_laudo(self, id_requisicao: str, drone_id: str, base_id: str, setor_id: str, tipo_ocorrencia: str, criticidade: str) -> Tuple[bool, str]:
        if not self._reconectar_se_necessario(): return False, "LEDGER_OFFLINE"
        try:
            timestamp = str(int(time.time()))
            args = [id_requisicao, drone_id, base_id, setor_id, timestamp, tipo_ocorrencia, str(criticidade)]
            resultado = self._invoke(self.CC_FN_REGISTRAR_LAUDO, args) or {}
            return resultado.get("sucesso", False), resultado.get("motivo", "OK")
        except Exception as e:
            logger.error("[LedgerClient] ERRO TRANSAÇÃO (registrar_laudo): %s", e)
            self._conectado = False
            return False, "ERRO_REDE"

    def auditoria_empresa(self, empresa_id: str) -> List[Dict[str, Any]]:
        if not self._reconectar_se_necessario(): return []
        try:
            resultado = self._query(self.CC_FN_AUDITORIA, [empresa_id]) or {}
            return resultado.get("transacoes", [])
        except Exception as e:
            logger.error("[LedgerClient] ERRO TRANSAÇÃO (auditoria_empresa): %s", e)
            self._conectado = False
            return []

    def criar_carteira(self, empresa_id: str, saldo_inicial: int) -> Tuple[bool, str]:
        if not self._reconectar_se_necessario(): return False, "LEDGER_OFFLINE"
        try:
            resultado = self._invoke(self.CC_FN_CRIAR_CARTEIRA, [empresa_id, str(saldo_inicial)]) or {}
            return resultado.get("sucesso", False), resultado.get("motivo", "OK")
        except Exception as e:
            logger.error("[LedgerClient] ERRO TRANSAÇÃO (criar_carteira): %s", e)
            self._conectado = False
            return False, "ERRO_REDE"

ledger = LedgerClient()