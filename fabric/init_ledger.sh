#!/bin/bash
# fabric/init_ledger.sh
# Inicializa as carteiras das 8 empresas de navegação no ledger.

set -e

ORDERER_CA="/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/ordererOrganizations/ormuz.com/orderers/orderer1.ormuz.com/tls/ca.crt"

echo "========================================"
echo "  ORMUZ — Inicialização do Ledger"
echo "========================================"
echo ""

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
      -o orderer1.ormuz.com:7050 \
      -C ormuz-channel \
      -n token_contract \
      --tls true \
      --cafile "$ORDERER_CA" \
      --peerAddresses peer0.norte.ormuz.com:7051 \
      --peerAddresses peer0.sul.ormuz.com:8051 \
      -c "{\"function\":\"CriarCarteira\",\"Args\":[\"$EMPRESA\",\"$SALDO\"]}"

  echo "  ✓ Carteira '$EMPRESA' criada com $SALDO tokens"
  sleep 1
done

echo ""
echo "========================================"
echo "  Ledger inicializado com sucesso!"
echo "========================================"