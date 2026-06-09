#!/bin/bash
# fabric/setup.sh
# Sobe toda a rede Hyperledger Fabric do zero.
# Execute a partir da raiz do projeto: bash fabric/setup.sh
#
# Pré-requisitos:
#   - Docker e Docker Compose instalados
#   - Binários do Fabric no PATH: cryptogen, configtxgen, peer
#   - Instale com: curl -sSL https://bit.ly/2ysbOFE | bash -s -- 2.5.0 1.5.7

set -e  # Para tudo se qualquer comando falhar

FABRIC_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$FABRIC_DIR")"

echo "========================================"
echo "  ORMUZ — Setup da Rede Fabric"
echo "========================================"

# ── 1. Limpeza de execuções anteriores ─────────────────────────────────────
echo ""
echo "[1/6] Limpando containers e volumes anteriores..."
cd "$PROJECT_ROOT/docker"
docker compose -f docker-compose.fabric.yml down --volumes --remove-orphans 2>/dev/null || true
rm -rf "$FABRIC_DIR/crypto-config"
rm -rf "$FABRIC_DIR/channel-artifacts"
mkdir -p "$FABRIC_DIR/channel-artifacts"

# ── 2. Geração dos certificados criptográficos ──────────────────────────────
echo ""
echo "[2/6] Gerando certificados MSP (cryptogen)..."
cd "$FABRIC_DIR"
cryptogen generate --config=./crypto-config.yaml
echo "  ✓ Certificados gerados em fabric/crypto-config/"

# ── 3. Geração dos artefatos do channel ─────────────────────────────────────
echo ""
echo "[3/6] Gerando artefatos do channel (configtxgen)..."
export FABRIC_CFG_PATH="$FABRIC_DIR"

# Bloco genesis do orderer
configtxgen \
  -profile OrmuzOrdererGenesis \
  -channelID system-channel \
  -outputBlock "$FABRIC_DIR/channel-artifacts/genesis.block"
echo "  ✓ genesis.block criado"

# Transação de criação do channel
configtxgen \
  -profile OrmuzChannel \
  -outputCreateChannelTx "$FABRIC_DIR/channel-artifacts/ormuz-channel.tx" \
  -channelID ormuz-channel
echo "  ✓ ormuz-channel.tx criado"

# Anchor peers de cada organização
for ORG in OrgNorte OrgSul OrgLeste OrgOeste; do
  configtxgen \
    -profile OrmuzChannel \
    -outputAnchorPeersUpdate "$FABRIC_DIR/channel-artifacts/${ORG}Anchors.tx" \
    -channelID ormuz-channel \
    -asOrg "${ORG}"
  echo "  ✓ ${ORG}Anchors.tx criado"
done

# ── 4. Subir containers ─────────────────────────────────────────────────────
echo ""
echo "[4/6] Subindo containers Docker (orderer + 4 peers + cli)..."
cd "$PROJECT_ROOT/docker"
docker compose -f docker-compose.fabric.yml up -d
echo "  ✓ Containers iniciados"
echo "  Aguardando 10s para estabilizar..."
sleep 10

# ── 5. Criar channel e fazer join dos peers ─────────────────────────────────
echo ""
echo "[5/6] Criando channel 'ormuz-channel' e conectando os peers..."

# Função auxiliar para executar comandos no peer correto via CLI
peer_exec() {
  local ORG=$1
  local PEER_ADDRESS=$2
  local MSP_ID=$3
  shift 3
  docker exec cli \
    env CORE_PEER_ADDRESS="$PEER_ADDRESS" \
        CORE_PEER_LOCALMSPID="$MSP_ID" \
        CORE_PEER_MSPCONFIGPATH="/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/${ORG}/users/Admin@${ORG}/msp" \
    "$@"
}

# OrgNorte cria o channel
peer_exec "norte.ormuz.com" "peer0.norte.ormuz.com:7051" "OrgNorteMSP" \
  peer channel create \
    -o orderer.ormuz.com:7050 \
    -c ormuz-channel \
    -f /opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts/ormuz-channel.tx
echo "  ✓ Channel 'ormuz-channel' criado"

# Cada peer entra no channel
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

# ── 6. Instalar e inicializar o chaincode ───────────────────────────────────
echo ""
echo "[6/6] Instalando chaincode 'token_contract'..."

# Empacota o chaincode
docker exec cli \
  peer lifecycle chaincode package /tmp/token.tar.gz \
    --path /opt/gopath/src/github.com/hyperledger/fabric/peer/chaincode \
    --lang golang \
    --label token_v1
echo "  ✓ Chaincode empacotado"

# Instala em cada peer
for DOMAIN in "${!PEERS[@]}"; do
  read -r ADDR MSP <<< "${PEERS[$DOMAIN]}"
  peer_exec "$DOMAIN" "$ADDR" "$MSP" \
    peer lifecycle chaincode install /tmp/token.tar.gz
  echo "  ✓ Chaincode instalado em peer0.${DOMAIN}"
done

# Obtém o Package ID gerado
PKG_ID=$(docker exec cli \
  peer lifecycle chaincode queryinstalled \
  | grep "token_v1" | awk -F 'Package ID: ' '{print $2}' | awk -F ',' '{print $1}')
echo "  Package ID: $PKG_ID"

# Cada organização aprova o chaincode
for DOMAIN in "${!PEERS[@]}"; do
  read -r ADDR MSP <<< "${PEERS[$DOMAIN]}"
  peer_exec "$DOMAIN" "$ADDR" "$MSP" \
    peer lifecycle chaincode approveformyorg \
      -o orderer.ormuz.com:7050 \
      --channelID ormuz-channel \
      --name token_contract \
      --version 1.0 \
      --package-id "$PKG_ID" \
      --sequence 1
  echo "  ✓ ${MSP} aprovou o chaincode"
done

# Commit do chaincode no channel
peer_exec "norte.ormuz.com" "peer0.norte.ormuz.com:7051" "OrgNorteMSP" \
  peer lifecycle chaincode commit \
    -o orderer.ormuz.com:7050 \
    --channelID ormuz-channel \
    --name token_contract \
    --version 1.0 \
    --sequence 1 \
    --peerAddresses peer0.norte.ormuz.com:7051 \
    --peerAddresses peer0.sul.ormuz.com:8051 \
    --peerAddresses peer0.leste.ormuz.com:9051 \
    --peerAddresses peer0.oeste.ormuz.com:10051
echo "  ✓ Chaincode commitado no channel"

echo ""
echo "========================================"
echo "  Rede Fabric pronta!"
echo "  Próximo passo: bash fabric/init_ledger.sh"
echo "========================================"