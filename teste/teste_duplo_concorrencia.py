"""
teste_duplo_gasto_ledger.py — Prova que o LEDGER (Hyperledger Fabric) impede
duplo gasto de verdade, não apenas a fila local de drones.

Diferença do teste/teste_concorrencia.py: aquele testa só a exclusao mutua
da FilaReplicada em memoria (Problema 2). Este aqui dispara duas chamadas
REAIS de AutorizarPagamento no chaincode, ao mesmo tempo, pra mesma
carteira, com saldo insuficiente pra autorizar as duas. So uma pode passar
— a prova fica registrada no proprio ledger.

Cenario:
    1. Cria uma carteira de teste com saldo pequeno e conhecido (10 tokens).
    2. Dispara DUAS requisicoes diferentes (id_requisicao distintos) tentando
       debitar 6 tokens cada da MESMA carteira, ao mesmo tempo (threads).
    3. Consulta o saldo final: se sobrou 4 (10-6), so uma passou — duplo
       gasto PREVENIDO. Se sobrasse -2 (10-12), seria um duplo gasto real
       (bug grave). Isso nunca deve acontecer, pois o Fabric usa controle
       de versao (MVCC) na escrita da chave da carteira.

Requisitos:
    - Rede Fabric já no ar (fabric/setup.sh executado).
    - Container 'cli' acessível via 'docker exec' (execute este script do
      mesmo host/PC onde você roda init_ledger.sh / transferir.sh).

Uso:
    python teste/teste_duplo_gasto_ledger.py
"""
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from typing import Dict, Optional

ORDERER_CA = (
    "/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/"
    "ordererOrganizations/ormuz.com/orderers/orderer1.ormuz.com/tls/ca.crt"
)
PEER_ADDRESSES = ["peer0.norte.ormuz.com:7051", "peer0.sul.ormuz.com:8051"]

SALDO_INICIAL = 10
CUSTO_CADA = 6  # 2 x 6 = 12 > 10 -> as duas NUNCA podem passar


def _chaincode_args(funcao: str, *args: str) -> str:
    return json.dumps({"function": funcao, "Args": list(args)})


def _docker_exec(args: list) -> "subprocess.CompletedProcess":
    return subprocess.run(["docker", "exec", "cli"] + args, capture_output=True, text=True)


def criar_carteira_teste(empresa_id: str, saldo: int) -> None:
    cmd = [
        "peer", "chaincode", "invoke",
        "-o", "orderer1.ormuz.com:7050",
        "-C", "ormuz-channel",
        "-n", "token_contract",
        "--tls", "true",
        "--cafile", ORDERER_CA,
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
    """Tenta achar o JSON de retorno embutido no log do 'peer chaincode invoke'.
    Se o formato da sua versao do Fabric for diferente e isso falhar, a
    saida bruta ainda é impressa no relatorio final pra inspecao manual —
    a conclusao do teste depende do saldo final consultado, nao deste parser.
    """
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
    for addr in PEER_ADDRESSES:
        cmd += ["--peerAddresses", addr]
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
    time.sleep(1)  # deixa o bloco da criacao confirmar antes da corrida

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
    time.sleep(1)
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

    print("\n--- Saida bruta da requisicao A (ultimas linhas) ---")
    print(resultados["A"]["saida_bruta"][-600:])
    print("\n--- Saida bruta da requisicao B (ultimas linhas) ---")
    print(resultados["B"]["saida_bruta"][-600:])


if __name__ == "__main__":
    main()