#!/bin/bash
# fabric/init_ledger.sh
# Inicializa as carteiras das 8 empresas de navegação no ledger.
# Execute APÓS o setup.sh: bash fabric/init_ledger.sh

set -e

# ==============================================================================
# PROTEÇÃO CONTRA O GIT BASH NO WINDOWS E DEFINIÇÃO DE CAMINHOS TLS
# ==============================================================================
export MSYS_NO_PATHCONV=1

ORDERER_CA="//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/ordererOrganizations/ormuz.com/orderers/orderer1.ormuz.com/tls/ca.crt"

# Certificados raiz (CA) dos dois peers que vão endossar (assinar) a transação
PEER_NORTE_CA="//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/norte.ormuz.com/peers/peer0.norte.ormuz.com/tls/ca.crt"
PEER_SUL_CA="//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/sul.ormuz.com/peers/peer0.sul.ormuz.com/tls/ca.crt"

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

# Mapeamento para garantir que cada empresa pertence a um consórcio diferente
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

  # Identifica a porta do peer dono da carteira para comunicação gRPC
  if [ "$ORG" == "norte" ]; then PORT=7051
  elif [ "$ORG" == "sul" ]; then PORT=8051
  elif [ "$ORG" == "leste" ]; then PORT=9051
  elif [ "$ORG" == "oeste" ]; then PORT=10051
  fi

  echo "Criando carteira: $EMPRESA (saldo=$SALDO tokens)..."
  echo " ↳ Assinando transação com a identidade: $MSP"

  # Invocação da transação de criação
  # A flag --tlsRootCertFiles agora aparece duas vezes, casando perfeitamente com os --peerAddresses
  docker exec \
    -e CORE_PEER_LOCALMSPID="$MSP" \
    -e CORE_PEER_TLS_ENABLED=true \
    -e CORE_PEER_ADDRESS="peer0.${ORG}.ormuz.com:${PORT}" \
    -e CORE_PEER_TLS_ROOTCERT_FILE="//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/${ORG}.ormuz.com/peers/peer0.${ORG}.ormuz.com/tls/ca.crt" \
    -e CORE_PEER_MSPCONFIGPATH="//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/${ORG}.ormuz.com/users/Admin@${ORG}.ormuz.com/msp" \
    cli \
    peer chaincode invoke \
      -o orderer1.ormuz.com:7050 \
      -C ormuz-channel \
      -n token_contract \
      --tls true \
      --cafile "$ORDERER_CA" \
      --peerAddresses peer0.norte.ormuz.com:7051 \
      --tlsRootCertFiles "$PEER_NORTE_CA" \
      --peerAddresses peer0.sul.ormuz.com:8051 \
      --tlsRootCertFiles "$PEER_SUL_CA" \
      -c "{\"function\":\"CriarCarteira\",\"Args\":[\"$EMPRESA\",\"$SALDO\"]}"

  echo "  ✓ Carteira '$EMPRESA' criada com sucesso! (Dono: $MSP)"
  sleep 2 # Aguarda o bloco ser commitado antes do próximo invoke para evitar conflito de MVCC
done

echo ""
echo "========================================"
echo "  Ledger inicializado com identidades descentralizadas!"
echo "========================================"