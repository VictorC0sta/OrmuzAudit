#!/bin/bash
# fabric/setup.sh
# Sobe toda a rede Hyperledger Fabric do zero.

set -e

# ==============================================================================
# PROTEÇÃO CONTRA O GIT BASH NO WINDOWS & MODO OFFLINE
# ==============================================================================
export MSYS_NO_PATHCONV=1
export GOFLAGS=""

FABRIC_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$FABRIC_DIR")"

export PATH=$PATH:$PROJECT_ROOT/bin

# BARRA DUPLA (//opt/...) PARA O GIT BASH NÃO INJETAR O "C:/Program Files/"
ORDERER_CA="//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/ordererOrganizations/ormuz.com/orderers/orderer1.ormuz.com/tls/ca.crt"

# NOVA POLÍTICA DE ENDOSSO: 2 de 4 Organizações devem assinar para o bloco ser válido
SIGNATURE_POLICY="OutOf(2, 'OrgNorteMSP.peer', 'OrgSulMSP.peer', 'OrgLesteMSP.peer', 'OrgOesteMSP.peer')"

echo "========================================"
echo "  ORMUZ — Setup da Rede Fabric (Raft, 3 orderers)"
echo "========================================"

echo ""
echo "[1/6] Limpando containers e volumes anteriores..."
cd "$PROJECT_ROOT/docker"
docker compose -f docker-compose.fabric.yml down --volumes --remove-orphans 2>/dev/null || true
rm -rf "$FABRIC_DIR/crypto-config"
rm -rf "$FABRIC_DIR/channel-artifacts"
mkdir -p "$FABRIC_DIR/channel-artifacts"

echo ""
echo "[2/6] Gerando certificados MSP + TLS (cryptogen) para 3 orderers e 4 peers..."
cd "$FABRIC_DIR"
cryptogen generate --config=./crypto-config.yaml
find crypto-config -type f -name "config.yaml" -exec sed -i 's|\\|/|g' {} +

echo "  ✓ Certificados gerados em fabric/crypto-config/"

echo ""
echo "[3/6] Gerando artefatos do channel (configtxgen) — bloco gênese já com os 3 consenters Raft..."
cd "$FABRIC_DIR"

configtxgen -profile OrmuzOrdererGenesis -channelID system-channel -outputBlock ./channel-artifacts/genesis.block
echo "  ✓ genesis.block criado"

configtxgen -profile OrmuzChannel -outputCreateChannelTx ./channel-artifacts/ormuz-channel.tx -channelID ormuz-channel
echo "  ✓ ormuz-channel.tx criado"

configtxgen -profile OrmuzChannel -outputAnchorPeersUpdate ./channel-artifacts/OrgNorteAnchors.tx -channelID ormuz-channel -asOrg OrgNorte
echo "  ✓ OrgNorteAnchors.tx criado"

configtxgen -profile OrmuzChannel -outputAnchorPeersUpdate ./channel-artifacts/OrgSulAnchors.tx -channelID ormuz-channel -asOrg OrgSul
echo "  ✓ OrgSulAnchors.tx criado"

configtxgen -profile OrmuzChannel -outputAnchorPeersUpdate ./channel-artifacts/OrgLesteAnchors.tx -channelID ormuz-channel -asOrg OrgLeste
echo "  ✓ OrgLesteAnchors.tx criado"

configtxgen -profile OrmuzChannel -outputAnchorPeersUpdate ./channel-artifacts/OrgOesteAnchors.tx -channelID ormuz-channel -asOrg OrgOeste
echo "  ✓ OrgOesteAnchors.tx criado"

echo ""
echo "[4/6] Subindo containers Docker (3 orderers Raft + 4 peers + cli)..."
cd "$PROJECT_ROOT/docker"
docker compose -f docker-compose.fabric.yml up -d
echo "  ✓ Containers iniciados"
echo "  Aguardando 15s para o cluster Raft eleger um líder..."
sleep 15

echo ""
echo "[5/6] Criando channel 'ormuz-channel' e conectando os peers..."

peer_exec() {
  local ORG=$1
  local PEER_ADDRESS=$2
  local MSP_ID=$3
  shift 3
  docker exec cli \
    env CORE_PEER_ADDRESS="$PEER_ADDRESS" \
        CORE_PEER_LOCALMSPID="$MSP_ID" \
        CORE_PEER_CLIENTCONNTIMEOUT=300s \
        CORE_PEER_DELIVERYCLIENTCONNTIMEOUT=300s \
        CORE_PEER_TLS_ENABLED=true \
        CORE_PEER_TLS_ROOTCERT_FILE="//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/${ORG}/peers/peer0.${ORG}/tls/ca.crt" \
        CORE_PEER_MSPCONFIGPATH="//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/${ORG}/users/Admin@${ORG}/msp" \
    "$@"
}

peer_exec "norte.ormuz.com" "peer0.norte.ormuz.com:7051" "OrgNorteMSP" \
  peer channel create \
    -o orderer1.ormuz.com:7050 \
    -c ormuz-channel \
    -f //opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts/ormuz-channel.tx \
    --tls true \
    --cafile "$ORDERER_CA"
echo "  ✓ Channel 'ormuz-channel' criado"

declare -A PEERS=(
  ["norte.ormuz.com"]="peer0.norte.ormuz.com:7051 OrgNorteMSP"
  ["sul.ormuz.com"]="peer0.sul.ormuz.com:8051 OrgSulMSP"
  ["leste.ormuz.com"]="peer0.leste.ormuz.com:9051 OrgLesteMSP"
  ["oeste.ormuz.com"]="peer0.oeste.ormuz.com:10051 OrgOesteMSP"
)

for DOMAIN in "${!PEERS[@]}"; do
  read -r ADDR MSP <<< "${PEERS[$DOMAIN]}"
  peer_exec "$DOMAIN" "$ADDR" "$MSP" \
    peer channel join -b ormuz-channel.block
  echo "  ✓ peer0.${DOMAIN} entrou no channel"
done

echo ""
echo "[6/6] Instalando chaincode 'token_contract'..."

# Injeta -mod=vendor para garantir o build offline isolado
docker exec cli \
  env GOFLAGS="-mod=vendor" \
  peer lifecycle chaincode package /tmp/token.tar.gz \
    --path //opt/gopath/src/github.com/hyperledger/fabric/peer/chaincode \
    --lang golang \
    --label token_v1
echo "  ✓ Chaincode empacotado"

for DOMAIN in "${!PEERS[@]}"; do
  read -r ADDR MSP <<< "${PEERS[$DOMAIN]}"
  peer_exec "$DOMAIN" "$ADDR" "$MSP" \
    peer lifecycle chaincode install /tmp/token.tar.gz
  echo "  ✓ Chaincode instalado em peer0.${DOMAIN}"
done

# Recuperando o Package ID com as variáveis TLS explícitas
PKG_ID=$(docker exec \
  -e CORE_PEER_TLS_ENABLED=true \
  -e CORE_PEER_TLS_ROOTCERT_FILE="//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/norte.ormuz.com/peers/peer0.norte.ormuz.com/tls/ca.crt" \
  cli peer lifecycle chaincode queryinstalled \
  | grep "token_v1" | awk -F 'Package ID: ' '{print $2}' | awk -F ',' '{print $1}')
echo "  Package ID: $PKG_ID"

for DOMAIN in "${!PEERS[@]}"; do
  read -r ADDR MSP <<< "${PEERS[$DOMAIN]}"
  peer_exec "$DOMAIN" "$ADDR" "$MSP" \
    peer lifecycle chaincode approveformyorg \
      -o orderer1.ormuz.com:7050 \
      --channelID ormuz-channel \
      --name token_contract \
      --version 1.0 \
      --package-id "$PKG_ID" \
      --sequence 1 \
      --signature-policy "$SIGNATURE_POLICY" \
      --waitForEventTimeout 300s \
      --tls true \
      --cafile "$ORDERER_CA"
  echo "  ✓ ${MSP} aprovou o chaincode (Política: 2 de 4)"
done

# Commit final exigindo o ca.crt de todos os 4 peers
peer_exec "norte.ormuz.com" "peer0.norte.ormuz.com:7051" "OrgNorteMSP" \
  peer lifecycle chaincode commit \
    -o orderer1.ormuz.com:7050 \
    --channelID ormuz-channel \
    --name token_contract \
    --version 1.0 \
    --sequence 1 \
    --signature-policy "$SIGNATURE_POLICY" \
    --waitForEventTimeout 30s \
    --tls true \
    --cafile "$ORDERER_CA" \
    --peerAddresses peer0.norte.ormuz.com:7051 \
    --tlsRootCertFiles "//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/norte.ormuz.com/peers/peer0.norte.ormuz.com/tls/ca.crt" \
    --peerAddresses peer0.sul.ormuz.com:8051 \
    --tlsRootCertFiles "//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/sul.ormuz.com/peers/peer0.sul.ormuz.com/tls/ca.crt" \
    --peerAddresses peer0.leste.ormuz.com:9051 \
    --tlsRootCertFiles "//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/leste.ormuz.com/peers/peer0.leste.ormuz.com/tls/ca.crt" \
    --peerAddresses peer0.oeste.ormuz.com:10051 \
    --tlsRootCertFiles "//opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/oeste.ormuz.com/peers/peer0.oeste.ormuz.com/tls/ca.crt"
echo "  ✓ Chaincode commitado no channel (Política: 2 de 4)"

echo ""
echo "========================================"
echo "  Rede Fabric configurada com sucesso!"