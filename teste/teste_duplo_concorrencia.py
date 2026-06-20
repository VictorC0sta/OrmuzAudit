import json
import re
import subprocess
import sys
import threading
import time
import uuid
from typing import Dict, Optional

# --- CAMINHOS DOS CERTIFICADOS DENTRO DO CONTAINER CLI ---
ORDERER_CA = (
    "/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/"
    "ordererOrganizations/ormuz.com/orderers/orderer1.ormuz.com/tls/ca.crt"
)

PEER_CA_NORTE = (
    "/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/"
    "peerOrganizations/norte.ormuz.com/peers/peer0.norte.ormuz.com/tls/ca.crt"
)

PEER_CA_SUL = (
    "/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/"
    "peerOrganizations/sul.ormuz.com/peers/peer0.sul.ormuz.com/tls/ca.crt"
)
# ---------------------------------------------------------

SALDO_INICIAL = 10
CUSTO_CADA = 6  # 2 x 6 = 12 > 10 -> as duas NUNCA podem passar


def _chaincode_args(funcao: str, *args: str) -> str:
    return json.dumps({"function": funcao, "Args": list(args)})


def _docker_exec(args: list) -> "subprocess.CompletedProcess":
    # INJETAMOS O CERTIFICADO DIRETAMENTE NA CHAMADA PARA O CLI NÃO SE PERDER
    cmd = ["docker", "exec", "-e", f"CORE_PEER_TLS_ROOTCERT_FILE={PEER_CA_NORTE}", "cli"] + args
    return subprocess.run(cmd, capture_output=True, text=True)


def criar_carteira_teste(empresa_id: str, saldo: int) -> None:
    cmd = [
        "peer", "chaincode", "invoke",
        "-o", "orderer1.ormuz.com:7050",
        "-C", "ormuz-channel",
        "-n", "token_contract",
        "--tls", "true",
        "--cafile", ORDERER_CA,
        # EXIGINDO ASSINATURAS DO NORTE E SUL PARA A CARTEIRA NASCER DE FATO NO LEDGER
        "--peerAddresses", "peer0.norte.ormuz.com:7051", "--tlsRootCertFiles", PEER_CA_NORTE,
        "--peerAddresses", "peer0.sul.ormuz.com:8051", "--tlsRootCertFiles", PEER_CA_SUL,
        "-c", _chaincode_args("CriarCarteira", empresa_id, str(saldo)),
    ]
    resultado = _docker_exec(cmd)
    if resultado.returncode != 0:
        print(resultado.stderr)
        raise RuntimeError(f"Falha ao criar carteira de teste '{empresa_id}'. "
                           f"A rede Fabric esta no ar? (fabric/setup.sh)")
    print(f"  Carteira de teste '{empresa_id}' criada com saldo={saldo}")


def consultar_saldo(empresa_id: str) -> int:
    cmd = [
        "peer", "chaincode", "query",
        "-C", "ormuz-channel",
        "-n", "token_contract",
        "-c", _chaincode_args("ConsultarSaldo", empresa_id),
    ]
    resultado = _docker_exec(cmd)
    if resultado.returncode != 0:
        print(resultado.stderr)
        raise RuntimeError(f"Falha ao consultar saldo de '{empresa_id}'")
    carteira = json.loads(resultado.stdout.strip())
    return carteira["saldo"]


def _extrair_payload(saida: str) -> Optional[dict]:
    m = re.search(r'payload:"(.*?)"\s*$', saida.strip(), re.MULTILINE)
    if not m:
        return None
    try:
        bruto = m.group(1).encode().decode("unicode_escape")
        return json.loads(bruto)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def autorizar_pagamento(id_requisicao: str, empresa_id: str, custo: int,
                        resultados: Dict[str, dict], chave: str) -> None:
    cmd = ["peer", "chaincode", "invoke",
           "-o", "orderer1.ormuz.com:7050",
           "-C", "ormuz-channel",
           "-n", "token_contract",
           "--tls", "true",
           "--cafile", ORDERER_CA]

    # EXIGINDO ASSINATURAS DO NORTE E SUL PARA A TRANSFERÊNCIA
    cmd += ["--peerAddresses", "peer0.norte.ormuz.com:7051", "--tlsRootCertFiles", PEER_CA_NORTE]
    cmd += ["--peerAddresses", "peer0.sul.ormuz.com:8051", "--tlsRootCertFiles", PEER_CA_SUL]

    cmd += ["-c", _chaincode_args("AutorizarPagamento", id_requisicao, empresa_id, str(custo))]

    inicio = time.time()
    resultado = _docker_exec(cmd)
    duracao = time.time() - inicio

    saida_completa = (resultado.stdout or "") + "\n" + (resultado.stderr or "")
    payload = _extrair_payload(saida_completa)

    resultados[chave] = {
        "returncode": resultado.returncode,
        "payload": payload,
        "duracao_s": round(duracao, 3),
        "saida_bruta": saida_completa.strip(),
    }


def main() -> None:
    empresa_teste = f"EMPRESA-TESTE-DUPLOGASTO-{uuid.uuid4().hex[:6]}"
    req_a = f"REQ-A-{uuid.uuid4().hex[:8]}"
    req_b = f"REQ-B-{uuid.uuid4().hex[:8]}"

    print("=" * 64)
    print("  TESTE DE PREVENCAO DE DUPLO GASTO NO LEDGER (Hyperledger Fabric)")
    print("=" * 64)
    print(f"\nEmpresa de teste:      {empresa_teste}")
    print(f"Saldo inicial:         {SALDO_INICIAL} tokens")
    print(f"Custo por requisicao:  {CUSTO_CADA} tokens  (2 x {CUSTO_CADA} = {2*CUSTO_CADA} > {SALDO_INICIAL} "
          f"-> as duas NAO podem passar)\n")

    print("[1/3] Criando carteira de teste...")
    criar_carteira_teste(empresa_teste, SALDO_INICIAL)
    time.sleep(2)  # Aumentei para 2s para garantir que os peers sincronizem o bloco antes da corrida

    print("\n[2/3] Disparando DUAS requisicoes SIMULTANEAS contra a mesma carteira...")
    print(f"   thread A -> id_requisicao={req_a}")
    print(f"   thread B -> id_requisicao={req_b}\n")

    resultados: Dict[str, dict] = {}
    t_a = threading.Thread(target=autorizar_pagamento, args=(req_a, empresa_teste, CUSTO_CADA, resultados, "A"))
    t_b = threading.Thread(target=autorizar_pagamento, args=(req_b, empresa_teste, CUSTO_CADA, resultados, "B"))

    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    for chave in ("A", "B"):
        r = resultados[chave]
        sucesso = r["payload"].get("sucesso") if r["payload"] else None
        motivo = r["payload"].get("motivo") if r["payload"] else "(nao foi possivel extrair o payload automaticamente — ver saida bruta abaixo)"
        print(f"  Requisicao {chave}: codigo_saida={r['returncode']} | tempo={r['duracao_s']}s | sucesso={sucesso} | motivo={motivo}")

    print("\n[3/3] Consultando saldo final no ledger...")
    time.sleep(2) # Mais 2s para o orderer confirmar os blocos de transação
    saldo_final = consultar_saldo(empresa_teste)
    print(f"  Saldo final de {empresa_teste}: {saldo_final} tokens\n")

    saldo_se_ambas_passassem = SALDO_INICIAL - (2 * CUSTO_CADA)
    saldo_se_uma_passou = SALDO_INICIAL - CUSTO_CADA

    print("=" * 64)
    if saldo_final == saldo_se_ambas_passassem:
        print("  FALHA CRITICA: as duas requisicoes foram autorizadas!")
        print("  Isso e um duplo gasto real — investigar o chaincode.")
        sys.exit(1)
    elif saldo_final == saldo_se_uma_passou:
        print("  RESULTADO: exatamente UMA requisicao foi autorizada.")
        print("  A outra foi rejeitada pelo ledger (saldo insuficiente e/ou")
        print("  conflito de versao MVCC) -> duplo gasto PREVENIDO.")
    elif saldo_final == SALDO_INICIAL:
        print("  RESULTADO (caso raro): as DUAS foram rejeitadas por conflito")
        print("  de concorrencia simultaneo no Fabric. Nenhum duplo gasto ocorreu.")
        print("  (Rode de novo se quiser ver o caso mais comum de exatamente 1 passar.)")
    else:
        print(f"  RESULTADO inesperado (saldo final={saldo_final}).")
        print("  Verifique manualmente a saida bruta de cada chamada abaixo.")
    print("=" * 64)

if __name__ == "__main__":
    main()