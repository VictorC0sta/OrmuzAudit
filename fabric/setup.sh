#!/bin/bash
# fabric/setup.sh
# Sobe toda a rede Hyperledger Fabric usando o modelo CCaaS.

set -e

FABRIC_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$FABRIC_DIR")"

ORDERER_CA="/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/ordererOrganizations/ormuz.com/orderers/orderer1.ormuz.com/tls/ca.crt"
SIGNATURE_POLICY="OutOf(2, 'OrgNorteMSP.peer', 'OrgSulMSP.peer', 'OrgLesteMSP.peer', 'OrgOesteMSP.peer')"

echo "========================================"
echo "  ORMUZ — Setup da Rede Fabric (CCaaS)"
echo "========================================"

echo ""
echo "[1/6] Limpando containers e volumes anteriores..."
cd "$PROJECT_ROOT/docker"
docker compose -f docker-compose.fabric.yml down --volumes --remove-orphans 2>/dev/null || true
rm -rf "$FABRIC_DIR/crypto-config" "$FABRIC_DIR/channel-artifacts"
mkdir -p "$FABRIC_DIR/channel-artifacts"

echo ""
echo "[2/6] Gerando certificados MSP + TLS..."
cd "$FABRIC_DIR"
cryptogen generate --config=./crypto-config.yaml

echo ""
echo "[3/6] Gerando artefatos do channel (Raft)..."
export FABRIC_CFG_PATH="$FABRIC_DIR"
configtxgen -profile OrmuzOrdererGenesis -channelID system-channel -outputBlock "$FABRIC_DIR/channel-artifacts/genesis.block"
configtxgen -profile OrmuzChannel -outputCreateChannelTx "$FABRIC_DIR/channel-artifacts/ormuz-channel.tx" -channelID ormuz-channel

for ORG in OrgNorte OrgSul OrgLeste OrgOeste; do
  configtxgen -profile OrmuzChannel -outputAnchorPeersUpdate "$FABRIC_DIR/channel-artifacts/${ORG}Anchors.tx" -channelID ormuz-channel -asOrg "${ORG}"
done

echo ""
echo "[4/6] Subindo containers Docker (Orderers + Peers + CLI)..."
cd "$PROJECT_ROOT/docker"
# Não sobe os chaincodes ainda!
docker compose -f docker-compose.fabric.yml up -d orderer1.ormuz.com orderer2.ormuz.com orderer3.ormuz.com peer0.norte.ormuz.com peer0.sul.ormuz.com peer0.leste.ormuz.com peer0.oeste.ormuz.com cli
sleep 15

echo ""
echo "[5/6] Criando channel 'ormuz-channel' e conectando os peers..."
peer_exec() {
  local ORG=$1; local PEER_ADDRESS=$2; local MSP_ID=$3; shift 3
  docker exec cli env CORE_PEER_ADDRESS="$PEER_ADDRESS" CORE_PEER_LOCALMSPID="$MSP_ID" CORE_PEER_MSPCONFIGPATH="/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/${ORG}/users/Admin@${ORG}/msp" "$@"
}

peer_exec "norte.ormuz.com" "peer0.norte.ormuz.com:7051" "OrgNorteMSP" peer channel create -o orderer1.ormuz.com:7050 -c ormuz-channel -f /opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts/ormuz-channel.tx --tls true --cafile "$ORDERER_CA"

declare -A PEERS=(
  ["norte.ormuz.com"]="peer0.norte.ormuz.com:7051 OrgNorteMSP"
  ["sul.ormuz.com"]="peer0.sul.ormuz.com:8051 OrgSulMSP"
  ["leste.ormuz.com"]="peer0.leste.ormuz.com:9051 OrgLesteMSP"
  ["oeste.ormuz.com"]="peer0.oeste.ormuz.com:10051 OrgOesteMSP"
)

for DOMAIN in "${!PEERS[@]}"; do
  read -r ADDR MSP <<< "${PEERS[$DOMAIN]}"
  peer_exec "$DOMAIN" "$ADDR" "$MSP" peer channel join -b ormuz-channel.block
done

echo ""
echo "[6/6] Preparando e Instalando Chaincode-as-a-Service (CCaaS)..."
cd "$FABRIC_DIR"
rm -rf ccaas_pkg && mkdir -p ccaas_pkg
cd ccaas_pkg

declare -A PKG_IDS

for DOMAIN in "${!PEERS[@]}"; do
  ORG_PREFIX=$(echo $DOMAIN | cut -d'.' -f1)
  
  cat <<EOF > metadata.json
{ "type": "ccaas", "label": "token_v1_${ORG_PREFIX}" }
EOF

  cat <<EOF > connection.json
{ "address": "ccaas.${ORG_PREFIX}.ormuz.com:9999", "dial_timeout": "10s", "tls_required": false }
EOF

  tar cfz code.tar.gz connection.json
  tar cfz token_${ORG_PREFIX}.tar.gz metadata.json code.tar.gz
  docker cp token_${ORG_PREFIX}.tar.gz cli:/tmp/

  read -r ADDR MSP <<< "${PEERS[$DOMAIN]}"
  peer_exec "$DOMAIN" "$ADDR" "$MSP" peer lifecycle chaincode install /tmp/token_${ORG_PREFIX}.tar.gz
  
  # Extrai o ID exato que o Fabric associou a este pacote
  PKG_ID=$(docker exec cli peer lifecycle chaincode queryinstalled | grep "token_v1_${ORG_PREFIX}" | awk -F 'Package ID: ' '{print $2}' | awk -F ',' '{print $1}')
  PKG_IDS[$ORG_PREFIX]=$PKG_ID
  echo "  ✓ Instalado em $ORG_PREFIX -> Package ID: $PKG_ID"

  peer_exec "$DOMAIN" "$ADDR" "$MSP" peer lifecycle chaincode approveformyorg -o orderer1.ormuz.com:7050 --channelID ormuz-channel --name token_contract --version 1.0 --package-id "$PKG_ID" --sequence 1 --signature-policy "$SIGNATURE_POLICY" --tls true --cafile "$ORDERER_CA"
done

peer_exec "norte.ormuz.com" "peer0.norte.ormuz.com:7051" "OrgNorteMSP" peer lifecycle chaincode commit -o orderer1.ormuz.com:7050 --channelID ormuz-channel --name token_contract --version 1.0 --sequence 1 --signature-policy "$SIGNATURE_POLICY" --tls true --cafile "$ORDERER_CA" --peerAddresses peer0.norte.ormuz.com:7051 --peerAddresses peer0.sul.ormuz.com:8051 --peerAddresses peer0.leste.ormuz.com:9051 --peerAddresses peer0.oeste.ormuz.com:10051

echo ""
echo "[7/7] Subindo os servidores de Chaincode (CCaaS)..."
cd "$PROJECT_ROOT/docker"
# Exportamos as variáveis para o Docker Compose instanciar os containers com a ID de segurança correta
export CHAINCODE_ID_NORTE=${PKG_IDS[norte]}
export CHAINCODE_ID_SUL=${PKG_IDS[sul]}
export CHAINCODE_ID_LESTE=${PKG_IDS[leste]}
export CHAINCODE_ID_OESTE=${PKG_IDS[oeste]}

docker compose -f docker-compose.fabric.yml up -d --build \
  ccaas.norte.ormuz.com ccaas.sul.ormuz.com ccaas.leste.ormuz.com ccaas.oeste.ormuz.com

echo ""
echo "================================================="
echo "  Rede Fabric e CCaaS configurados com sucesso!"
