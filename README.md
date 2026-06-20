# Estreito de Ormuz: Central de Comando, Economia e Auditoria de Guerra

### Central de Monitoramento Marítimo e Ledger Distribuído Permissionado para Gestão e Log Imutável de Ativos

*Disciplina TEC502 — Sistemas Distribuídos · Universidade Estadual de Feira de Santana (UEFS)*

---

## 📑 Índice / Sumário

1. [Visão Geral do Sistema](#1-visão-geral-do-sistema)
2. [Arquitetura em Duas Camadas (A Escolha do Design)](#2-arquitetura-em-duas-camadas-a-escolha-do-design)
3. [Infraestrutura da Blockchain e Topologia da Rede](#3-infraestrutura-da-blockchain-e-topologia-da-rede)
4. [Gestão Descentralizada de Ativos e Autenticação (A Gênese)](#4-gestão-descentralizada-de-ativos-e-autenticação-a-gênese)
5. [Fluxo de Pagamento e Resolução Definitiva de Duplo Gasto](#5-fluxo-de-pagamento-e-resolução-definitiva-de-duplo-gasto)
6. [Proteção Avançada contra Duplo Despacho (Race Conditions)](#6-proteção-avançada-contra-duplo-despacho-race-conditions)
7. [Log de Operações Imutável e Auditabilidade Visual](#7-log-de-operações-imutável-e-auditabilidade-visual)
8. [Estrutura de Pastas Atualizada](#8-estrutura-de-pastas-atualizada)
9. [Guião Completo de Execução e Testes Práticos](#9-guião-completo-de-execução-e-testes-práticos)
10. [Demonstrações de Defesa Essenciais (Gabarito do Barema)](#10-demonstrações-de-defesa-essenciais-gabarito-do-barema)

---

## 1. Visão Geral do Sistema

O sistema evoluiu de uma infraestrutura estritamente operacional para uma **arquitetura em duas camadas independentes**, projetada para resolver a falta de confiança mútua, a espionagem e a possibilidade de adulteração de logs entre nações concorrentes no Estreito de Ormuz.

A central coordena o monitoramento de **8 setores marítimos**, cada um pertencente a uma empresa de navegação comercial específica (`EMPRESA-A` a `EMPRESA-H`), utilizando uma frota de drones autônomos compartilhada entre **4 bases operacionais** (`Norte`, `Sul`, `Leste`, `Oeste`). Toda a camada de liquidação financeira, custeio de missões, prevenção de fraudes e log imutável de laudos é gerida nativamente por uma rede blockchain permissionada corporativa baseada em **Hyperledger Fabric**.

---

## 2. Arquitetura em Duas Camadas (A Escolha do Design)

O sistema adota um design híbrido desacoplado para equilibrar os requisitos conflitantes de tempo real e consistência forte:

```
                  ┌─────────────────────────────────────────┐
                  │          Sensor de Setor (S1..S8)       │
                  └────────────────────┬────────────────────┘
                                       │ Assinatura HMAC (Autenticação)
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │         Broker de Setor (S1..S8)        │
                  └────────────────────┬────────────────────┘
                                       │
                  ┌────────────────────┴────────────────────┐
                  │ Broadcast TCP Simultâneo (Requisição)   │
                  ▼                                         ▼
   ┌─────────────────────────────┐           ┌─────────────────────────────┐
   │     Broker da Base NORTE    │           │      Broker da Base SUL     │
   └──────────────┬──────────────┘           └──────────────┬──────────────┘
                  │                                         │
 ┌────────────────┼────────────────┐       ┌────────────────┼────────────────┐
 │ CAMADA 1:      │                │       │ CAMADA 1:      │                │
 │ Coordenação    │ Timeout TDMA   │       │ Coordenação    │ Timeout TDMA   │
 │ Operacional    │ Local          │       │ Operacional    │ Local          │
 │ (Tempo Real)   │                │       │ (Tempo Real)   │                │
 └────────────────┼────────────────┘       └────────────────┼────────────────┘
                  │                                         │
 ┌────────────────┼────────────────┐       ┌────────────────┼────────────────┐
 │ CAMADA 2:      │ gRPC           │       │ CAMADA 2:      │ gRPC           │
 │ Consenso Forte │ (Invoke)       │       │ Consenso Forte │ (Invoke)       │
 │ e Auditoria    ▼                │       │ e Auditoria    ▼                │
 │ (Blockchain) ┌────────────────┐ │       │ (Blockchain) ┌────────────────┐ │
 │              │ peer0.norte    │ │       │              │ peer0.sul      │ │
 └──────────────┼───────┬────────┼─┘       └──────────────┼───────┬────────┼─┘
                │       │        │                        │       │        │
                │       │        └───────────┐┌───────────┘       │        │
                │       │                    ││                   │        │
                │       ▼                    ▼▼                   ▼        │
                │  ┌────────────────────────────────────────────────────┐  │
                │  │       Cluster de Consenso Raft (3 Orderers)        │  │
                │  └────────────────────────────────────────────────────┘  │
                │                                                          │
                └──────────────────────────────────────────────────────────┘

```

### Justificação dos Trade-Offs (Defesa Acadêmica para a Arguição)

* **Camada 1: Coordenação Operacional (Baixa Latência):** O despacho de drones e a prevenção de colisões/alocações duplicadas na malha aérea exigem respostas na ordem de milissegundos. O mecanismo de **exclusão mútua por timeouts locais estáticos (TDMA)** e relógios de Lamport do Problema 2 foi mantido para fins operacionais. O consenso de uma blockchain (~1 a 2 segundos por bloco) é incompatível com o despacho de hardware em tempo real.
* **Camada 2: Liquidação e Auditoria (Consenso Forte):** O Hyperledger Fabric atua estritamente na gestão dos ativos e no log de laudos. Como os nós representam nações e bases militares conhecidas, uma blockchain *permissionada* é ideal: elimina o overhead de mineração e taxas voláteis (*gas*) de redes públicas (como Ethereum), provê identidades criptográficas via CAs e garante privacidade consorcial.

---

## 3. Infraestrutura da Blockchain e Topologia da Rede

A rede blockchain é composta por uma topologia de nível empresarial configurada via contêineres Docker interconectados na rede externa compartilhada.

* **Camada de Ordenação (Ordering Service):** Cluster de **3 nós Orderers rodando o protocolo Raft** (`orderer1`, `orderer2`, `orderer3.ormuz.com`), garantindo tolerância a falhas por travamento (CFT). Se um orderer falhar, a eleição interna do Raft estabelece um novo líder de forma transparente para as bases.
* **Camada de Endosso (Peers das Organizações):** **4 Peers independentes**, um para cada organização/base do consórcio internacional:
* `peer0.norte.ormuz.com` (OrgNorteMSP)
* `peer0.sul.ormuz.com` (OrgSulMSP)
* `peer0.leste.ormuz.com` (OrgLesteMSP)
* `peer0.oeste.ormuz.com` (OrgOesteMSP)


* **Política de Endosso Personalizada (Signature Policy):** O contrato foi implantado com a regra estrita `OutOf(2, 'OrgNorteMSP.peer', 'OrgSulMSP.peer', 'OrgLesteMSP.peer', 'OrgOesteMSP.peer')`. Qualquer alteração de estado (débito ou laudo) exige a assinatura digital e o aval de **pelo menos 2 organizações independentes**. Isso impede que uma única base maliciosa manipule dados de forma unilateral e garante que a rede sobreviva com tolerância total se 1 peer for completamente desligado durante a arguição do professor.

---

## 4. Gestão Descentralizada de Ativos e Autenticação (A Gênese)

Para mitigar o risco de uma "autoridade central disfarçada", a inicialização do ledger foi completamente descentralizada.

### Vinculação de Identidade Criptográfica (MSP)

O script de setup não utiliza uma identidade administrativa centralizada para gerar as carteiras. No momento da gênese, o script `fabric/init_ledger.sh` assume dinamicamente o par de chaves e o MSP da organização de controle da empresa correspondente:

* `EMPRESA-A` e `EMPRESA-B` $\rightarrow$ Registradas e assinadas via `OrgNorteMSP`
* `EMPRESA-C` e `EMPRESA-D` $\rightarrow$ Registradas e assinadas via `OrgSulMSP`
* `EMPRESA-E` e `EMPRESA-F` $\rightarrow$ Registradas e assinadas via `OrgLesteMSP`
* `EMPRESA-G` e `EMPRESA-H` $\rightarrow$ Registradas e assinadas via `OrgOesteMSP`

### Validação de Propriedade do Ativo

Dentro do Smart Contract (`chaincode/token_contract.go`), a função `TransferirTokens` captura a identidade do chamador em tempo de execução usando `ctx.GetClientIdentity().GetMSPID()`. O chaincode compara esse valor com o MSP gravado na criação da carteira. Uma tentativa de transferência originada pela Base Leste sobre ativos da Empresa A (Norte) resultará em uma rejeição imediata da transação diretamente na máquina de estado do Fabric.

---

## 5. Fluxo de Pagamento e Resolução Definitiva de Duplo Gasto

O sistema implementa um fluxo de pagamento **estritamente pré-pago**, eliminando a vulnerabilidade onde drones voavam de graça se a empresa ficasse sem saldo durante o percurso.

```
┌───────────────┐        1. ConsultarSaldo        ┌───────────────┐
│ Broker Setor  ├────────────────────────────────►  Peer Local   │
│ (Fail-Open)   ◄────────────────────────────────┤   do Fabric   │
└───────┬───────┘        Retorna Saldo/Status     └───────────────┘
        │
        │ 2. Broadcast de Requisição
        ▼
┌───────────────┐        3. AutorizarPagamento    ┌───────────────┐
│  Broker Base  ├────────────────────────────────►  Rede Fabric  │
│ (Vencedor)    ◄────────────────────────────────┤ (MVCC Block)  │
└───────┬───────┘        Sucesso / JaAutorizado   └───────────────┘
        │
        │ 4. Despacho Real (Se verificado OK)
        ▼
┌───────────────┐
│     Drone     │
└───────────────┘

```

1. **Pré-Filtragem Otimista com Fail-Open (Setor):** Ao receber um alerta do sensor, o Broker do Setor realiza uma consulta de leitura (`ConsultarSaldo`) no peer local designado para balanceamento de carga (S1/S2 consultam Norte; S3/S4 consultam Sul, etc.).
* *Mecanismo Fail-Open:* Se o peer local estiver offline, o Python captura o erro de conectividade e ativa a degradação graciosa: o alerta é encaminhado para as bases mesmo assim, impedindo que a falha de um único nó paralise a ingestão de dados.


2. **Débito Atômico e Bloqueio MVCC (Base):** Quando o cronômetro de prioridade TDMA de uma base zera, ela invoca a transação `AutorizarPagamento` no ledger **antes** de enviar o comando TCP ao drone.
* O Hyperledger Fabric processa o débito do custo operacional (CRÍTICA = 3, ALTA = 2, BAIXA = 1 token). Se duas bases tentarem debitar simultaneamente o saldo de uma empresa que possui fundos para apenas uma missão, o mecanismo de **Multi-Version Concurrency Control (MVCC)** do Fabric detectará o conflito de leitura/escrita na versão da chave da carteira, validando apenas uma transação e invalidando a concorrente no momento do commit do bloco.



---

## 6. Proteção Avançada contra Duplo Despacho (Race Conditions)

Uma falha clássica de redes P2P ocorre quando o broadcast de `ACEITE` de uma base atrasa devido à latência ou perda de pacotes, fazendo com que uma segunda base assuma a mesma missão erroneamente. O sistema neutraliza isso utilizando a blockchain como o **desempate central definitivo**:

* Ao criar uma autorização, o chaincode grava uma chave única no estado mundial baseada no ID da requisição: `PAG_ + id_requisicao`.
* Se o atraso de rede ocultar o estado de processamento operacional, e duas bases chamarem `AutorizarPagamento` para a mesma missão, a segunda chamada atingirá a chave existente.
* O Smart Contract Go intercepta isso e retorna uma resposta padronizada contendo a flag `"ja_autorizado": true`.
* O código Python da Base (`base/broker.py`), ao receber `"ja_autorizado": true`, verifica se aquela chamada faz parte de uma reemissão legítima (recuperação de drone caído via `is_reemissao`). Caso não seja, a Base identifica na fração de segundo que perdeu a corrida de consenso, cancela o acionamento e **aborta imediatamente o duplo-despacho**, preservando a frota.

---

## 7. Log de Operações Imutável e Auditabilidade Visual

Ao término de cada missão, o drone retorna à base e o Broker invoca a função `RegistrarLaudo`.

* **Conteúdo Enriquecido do Laudo:** O laudo gravado na blockchain não contém apenas metadados. Ele crava no estado imutável o `id_requisicao`, `drone_id`, `base_id`, `setor_id`, `timestamp` Unix e os dados analíticos coletados pelo sensor no momento da falha: `tipo_ocorrencia` (ex: bloqueio de rota, embarcação à deriva) e a `criticidade`.
* **Auditoria Visual Unificada:** O arquivo `monitor_bridge.py` foi transformado em um servidor WebSocket bidirecional assíncrono. Na aba **📊 Auditoria** do painel `index.html`, qualquer membro do consórcio pode selecionar uma empresa e realizar chamadas em tempo real à API do Fabric para auditar saldos e inspecionar o histórico completo de transações e hashes de blocos retornados pela função `GetHistoryForKey`, sem necessidade de acesso administrativo ao terminal.

---

## 8. Estrutura de Pastas Atualizada

```
.
├── base/
│   ├── broker.py             # Garante fluxo Pré-pago, intercepta duplo-despacho via ledger
│   ├── dockerfile            # Base com fabric-sdk-py pré-instalado
│   └── fila_replicada.py     # Gerencia estados operacionais locais
├── chaincode/
│   └── token_contract.go     # Smart Contract em Go: MVCC, MSP-checks, idempotência avançada
├── config/
│   └── custo_por_criticidade.json  # Tabela oficial de precificação de tokens
├── docker/
│   ├── .env                  # IPs das máquinas do laboratório e variáveis de ambiente
│   └── docker-compose.fabric.yml   # Definição dos 3 orderers Raft, 4 peers e CLI do Fabric
├── fabric/
│   ├── configtx.yaml         # Perfil do canal e definição das organizações consorciais
│   ├── crypto-config.yaml    # Configuração de geração de certificados MSP e TLS
│   ├── connection-profile.json # Perfil de conexão lido pelo ledger_client.py
│   ├── setup.sh              # Geração de artefatos, subida da rede e commit do chaincode com 2-de-4
│   └── init_ledger.sh        # Gênese descentralizada de carteiras assinando por Org respectiva
├── monitor/
│   ├── index.html            # UI com a aba Auditoria integrada para consultas e transferências P2P
│   └── monitor_bridge.py     # API assíncrona bidirecional (WebSocket <-> gRPC Fabric)
├── shared/
│   ├── ledger_client.py      # Wrapper nativo do fabric-sdk-py com tratamento de exceções de rede
│   └── protocolo.py          # Utilitários de rede TCP/UDP
└── teste/
    └── teste_duplo_concorrencia.py # Teste de estresse disparando débitos simultâneos contra MVCC

```

---

## 9. Guião Completo de Execução e Testes Práticos

Para a apresentação de 30 minutos no laboratório da UEFS, execute os comandos exatamente nesta sequência para demonstrar a conformidade total com o barema:

### Passo 1: Inicialização da Rede Blockchain (PC 2 - Servidor Fabric)

Execute o script de infraestrutura para gerar a topologia Raft e aplicar a política de endosso de duas organizações:

```bash
bash fabric/setup.sh

```

*Validação para o professor:* Para provar que a política de 2 de 4 foi aplicada com sucesso, execute:

```bash
docker exec cli peer lifecycle chaincode querycommitted --channelID ormuz-channel --name token_contract

```

### Passo 2: Gênese Descentralizada dos Ativos (PC 2)

Injete os tokens iniciais nas carteiras forçando a autenticação distribuída por organização dona:

```bash
bash fabric/init_ledger.sh

```

### Passo 3: Subida dos Componentes Operacionais

Nos seus respectivos PCs do laboratório, suba os demais blocos do sistema:

* **Bases (PC 2):** `docker compose -f docker/docker-compose.bases.yml up -d`
* **Setores (PC 1):** `docker compose -f docker/docker-compose.setores.yml up -d`
* **Drones (PC 3):** `docker compose -f docker/docker-compose.drones.yml up -d`

### Passo 4: Conexão do Monitor de Auditoria

Inicie o microsserviço da API de auditoria e abra o painel visual:

```bash
python monitor/monitor_bridge.py

```

Abra o arquivo `monitor/index.html` no navegador, vá à aba **Bridge**, clique em **Conectar**. Em seguida, navegue até a aba **📊 Auditoria** e clique em **Consultar todas no Fabric** para ver os saldos reais sincronizados diretamente da blockchain.

---

## 10. Demonstrações de Defesa Essenciais (Gabarito do Barema)

### Prova 1: Teste de Resiliência e Tolerância a Falhas (Derrubada de Nós)

Durante a arguição, o professor solicitará a derrubada de um nó para testar a robustez da rede.

```bash
# Desligue completamente o peer da organização Leste
docker stop peer0.leste.ormuz.com

```

*Comportamento esperado:* Vá ao painel web e dispare um alerta para um setor controlado pela Base Leste. Como nossa política exige 2 de 4 assinaturas, as bases remanescentes (`Norte`, `Sul`, `Oeste`) coletarão os endossos entre si, fecharão o bloco com sucesso via Raft e o drone decolará normalmente. O sistema demonstra resiliência total a falhas de componentes.

### Prova 2: Teste de Duplo Gasto e Concorrência Extrema

Para validar a segurança das carteiras sob alta concorrência:

1. Vá à aba **Stress Test** no monitor visual.
2. Inicie um bombardeamento massivo de alertas simultâneos direcionados à mesma empresa pagante com fundos limitados.
3. Inspecione o terminal de logs ou os logs do contêiner da base: os débitos excedentes serão rejeitados de forma limpa pelo MVCC do Fabric antes de gerar tráfego aéreo, emitindo o evento `PAGAMENTO_RECUSADO` diretamente no log distribuído do painel.
