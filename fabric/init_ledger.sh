#!/bin/bash
# fabric/init_ledger.sh
# Inicializa as carteiras das 8 empresas de navegação no ledger.
# Execute APÓS o setup.sh: bash fabric/init_ledger.sh
#
# Cada empresa começa com 100 tokens operacionais.
# Custo por requisição: CRITICA=3, ALTA=2, BAIXA=1

set -e

echo "========================================"
echo "  ORMUZ — Inicialização do Ledger"
echo "========================================"
echo ""

# Empresas e seus saldos iniciais (ajuste conforme necessário)
declare -A EMPRESAS=(
  ["EMPRESA-A"]=200
  ["EMPRESA-B"]=200
  ["EMPRESA-C"]=200
  ["EMPRESA-D"]=200
  ["EMPRESA-E"]=200
  ["EMPRESA-F"]=200
  ["EMPRESA-G"]=200
  ["EMPRESA-H"]=200
)

for EMPRESA in "${!EMPRESAS[@]}"; do
  SALDO="${EMPRESAS[$EMPRESA]}"
  echo "Criando carteira: $EMPRESA (saldo=$SALDO tokens)..."

  docker exec cli \
    peer chaincode invoke \
      -o orderer.ormuz.com:7050 \
      -C ormuz-channel \
      -n token_contract \
      --peerAddresses peer0.norte.ormuz.com:7051 \
      --peerAddresses peer0.sul.ormuz.com:8051 \
      -c "{\"function\":\"CriarCarteira\",\"Args\":[\"$EMPRESA\",\"$SALDO\"]}"

  echo "  ✓ Carteira '$EMPRESA' criada com $SALDO tokens"
  sleep 1  # Aguarda o bloco ser commitado antes do próximo invoke
done

echo ""
echo "========================================"
echo "  Ledger inicializado com sucesso!"
echo ""
echo "  Para verificar um saldo:"
echo "  docker exec cli peer chaincode query -C ormuz-channel -n token_contract \\"
echo "    -c '{\"function\":\"ConsultarSaldo\",\"Args\":[\"EMPRESA-A\"]}'"
echo "========================================"