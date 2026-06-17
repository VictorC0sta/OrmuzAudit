#!/bin/bash
# fabric/init_ledger.sh
# Inicializa as carteiras das 8 empresas de navegação no ledger.
# Execute APÓS o setup.sh: bash fabric/init_ledger.sh

set -e

ORDERER_CA="/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/ordererOrganizations/ormuz.com/orderers/orderer1.ormuz.com/tls/ca.crt"

echo "========================================"
echo "  ORMUZ — Inicialização do Ledger (Descentralizada)"
echo "========================================"
echo ""

# Empresas e seus saldos iniciais
declare -A EMPRESAS=(
  ["EMPRESA-A"]=200 ["EMPRESA-B"]=200
  ["EMPRESA-C"]=200 ["EMPRESA-D"]=200
  ["EMPRESA-E"]=200 ["EMPRESA-F"]=200
  ["EMPRESA-G"]=200 ["EMPRESA-H"]=200
)

# NOVO: Mapeamento para garantir que cada empresa pertence a um consórcio diferente
declare -A ORG_DA_EMPRESA=(
  ["EMPRESA-A"]="norte" ["EMPRESA-B"]="norte"
  ["EMPRESA-C"]="sul"   ["EMPRESA-D"]="sul"
  ["EMPRESA-E"]="leste" ["EMPRESA-F"]="leste"
  ["EMPRESA-G"]="oeste" ["EMPRESA-H"]="oeste"
)

# Iterando numa ordem fixa para manter o log organizado
for EMPRESA in "EMPRESA-A" "EMPRESA-B" "EMPRESA-C" "EMPRESA-D" "EMPRESA-E" "EMPRESA-F" "EMPRESA-G" "EMPRESA-H"; do
  SALDO="${EMPRESAS[$EMPRESA]}"
  ORG="${ORG_DA_EMPRESA[$EMPRESA]}"
  
  # Capitaliza a primeira letra para montar o nome do MSP (ex: norte -> OrgNorteMSP)
  MSP="Org$(tr '[:lower:]' '[:upper:]' <<< ${ORG:0:1})${ORG:1}MSP"

  echo "Criando carteira: $EMPRESA (saldo=$SALDO tokens)..."
  echo " ↳ Assinando transação com a identidade: $MSP"

  # NOVO: Executa a chamada injetando os certificados da organização dona da carteira
  docker exec \
    -e CORE_PEER_LOCALMSPID="$MSP" \
    -e CORE_PEER_MSPCONFIGPATH="/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/${ORG}.ormuz.com/users/Admin@${ORG}.ormuz.com/msp" \
    cli \
    peer chaincode invoke \
      -o orderer1.ormuz.com:7050 \
      -C ormuz-channel \
      -n token_contract \
      --tls true \
      --cafile "$ORDERER_CA" \
      --peerAddresses peer0.norte.ormuz.com:7051 \
      --peerAddresses peer0.sul.ormuz.com:8051 \
      -c "{\"function\":\"CriarCarteira\",\"Args\":[\"$EMPRESA\",\"$SALDO\"]}"

  echo "  ✓ Carteira '$EMPRESA' criada com sucesso! (Dono: $MSP)"
  sleep 1 # Aguarda o bloco ser commitado antes do próximo invoke
done

echo ""
echo "========================================"
echo "  Ledger inicializado com identidades descentralizadas!"
echo "========================================"