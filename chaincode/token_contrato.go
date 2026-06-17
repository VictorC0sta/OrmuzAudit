package main

import (
	"encoding/json"
	"fmt"
	"strconv"
	"time"

	"github.com/hyperledger/fabric-chaincode-go/pkg/cid"
	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// SmartContract provê as funções para interagir com o ledger
type SmartContract struct {
	contractapi.Contract
}

// ── ESTRUTURAS DE DADOS (Modelos) ──────────────────────────────────────────

// Carteira representa os fundos de uma empresa de navegação
type Carteira struct {
	IDEmpresa string `json:"id_empresa"`
	Saldo     int    `json:"saldo"`
	MSPID     string `json:"msp_id"` // Armazena a identidade do dono para autenticação
}

// LaudoMissao guarda o registro imutável do que o drone fez
type LaudoMissao struct {
	IDRequisicao   string `json:"id_requisicao"`
	DroneID        string `json:"drone_id"`
	BaseID         string `json:"base_id"`
	SetorID        string `json:"setor_id"`
	Timestamp      string `json:"timestamp"`
	TipoOcorrencia string `json:"tipo_ocorrencia"`
	Criticidade    string `json:"criticidade"`
}

// RespostaTransacao padroniza os retornos lidos pelo ledger_client.py
type RespostaTransacao struct {
	Sucesso       bool   `json:"sucesso"`
	Motivo        string `json:"motivo"`
	SaldoRestante int    `json:"saldo_restante,omitempty"`
}

// HistoricoTransacao é usado para montar a resposta da auditoria
type HistoricoTransacao struct {
	TxID      string `json:"tx_id"`
	Timestamp string `json:"timestamp"`
	Valor     int    `json:"saldo_momento"`
}

// ── 1. GESTÃO DE ATIVOS E AUTENTICAÇÃO ──────────────────────────────────────

// CriarCarteira inicializa uma empresa vinculando-a ao MSPID do criador
func (s *SmartContract) CriarCarteira(ctx contractapi.TransactionContextInterface, idEmpresa string, saldoInicialStr string) (*RespostaTransacao, error) {
	carteiraJSON, err := ctx.GetStub().GetState(idEmpresa)
	if err != nil {
		return nil, fmt.Errorf("falha ao ler o ledger: %v", err)
	}
	if carteiraJSON != nil {
		return &RespostaTransacao{Sucesso: false, Motivo: "carteira ja existe"}, nil
	}

	saldoInicial, err := strconv.Atoi(saldoInicialStr)
	if err != nil {
		return nil, fmt.Errorf("saldo invalido: %v", err)
	}

	// Captura o MSPID da organização que está assinando a criação
	clientMSPID, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return nil, fmt.Errorf("falha ao obter MSPID do criador: %v", err)
	}

	carteira := Carteira{IDEmpresa: idEmpresa, Saldo: saldoInicial, MSPID: clientMSPID}
	novaCarteiraJSON, _ := json.Marshal(carteira)

	err = ctx.GetStub().PutState(idEmpresa, novaCarteiraJSON)
	if err != nil {
		return nil, fmt.Errorf("falha ao salvar no ledger: %v", err)
	}

	return &RespostaTransacao{Sucesso: true, Motivo: "OK", SaldoRestante: saldoInicial}, nil
}

// ConsultarSaldo retorna os tokens atuais de uma empresa
func (s *SmartContract) ConsultarSaldo(ctx contractapi.TransactionContextInterface, idEmpresa string) (*Carteira, error) {
	carteiraJSON, err := ctx.GetStub().GetState(idEmpresa)
	if err != nil {
		return nil, fmt.Errorf("falha ao ler o ledger: %v", err)
	}
	if carteiraJSON == nil {
		return nil, fmt.Errorf("carteira %s nao existe", idEmpresa)
	}

	var carteira Carteira
	json.Unmarshal(carteiraJSON, &carteira)
	return &carteira, nil
}

// TransferirTokens move fundos garantindo que apenas o dono da origem assinou
func (s *SmartContract) TransferirTokens(ctx contractapi.TransactionContextInterface, idOrigem string, idDestino string, valorStr string) (*RespostaTransacao, error) {
	valor, err := strconv.Atoi(valorStr)
	if err != nil || valor <= 0 {
		return nil, fmt.Errorf("valor de transferencia invalido: %v", err)
	}

	origemJSON, err := ctx.GetStub().GetState(idOrigem)
	if err != nil || origemJSON == nil {
		return &RespostaTransacao{Sucesso: false, Motivo: "carteira de origem nao encontrada"}, nil
	}

	var carteiraOrigem Carteira
	json.Unmarshal(origemJSON, &carteiraOrigem)

	// Validação de Propriedade: O chamador precisa pertencer ao mesmo MSP da carteira de origem
	clientMSPID, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return nil, fmt.Errorf("falha ao verificar identidade do chamador: %v", err)
	}
	if carteiraOrigem.MSPID != clientMSPID {
		return &RespostaTransacao{Sucesso: false, Motivo: "transacao recusada: assinatura nao pertence ao dono dos ativos"}, nil
	}

	if carteiraOrigem.Saldo < valor {
		return &RespostaTransacao{Sucesso: false, Motivo: "saldo insuficiente na origem"}, nil
	}

	destinoJSON, err := ctx.GetStub().GetState(idDestino)
	if err != nil || destinoJSON == nil {
		return &RespostaTransacao{Sucesso: false, Motivo: "carteira de destino nao encontrada"}, nil
	}

	var carteiraDestino Carteira
	json.Unmarshal(destinoJSON, &carteiraDestino)

	carteiraOrigem.Saldo -= valor
	carteiraDestino.Saldo += valor

	novaOrigemJSON, _ := json.Marshal(carteiraOrigem)
	novoDestinoJSON, _ := json.Marshal(carteiraDestino)

	ctx.GetStub().PutState(idOrigem, novaOrigemJSON)
	ctx.GetStub().PutState(idDestino, novoDestinoJSON)

	return &RespostaTransacao{Sucesso: true, Motivo: "Transferencia concluida com sucesso", SaldoRestante: carteiraOrigem.Saldo}, nil
}

// ── 2. LOG DE OPERAÇÕES IMUTÁVEL E COBRANÇA ATÔMICA PÓS-MISSÃO ──────────────

// RegistrarConclusao liquida o pagamento e cria o Laudo de forma indissociável
func (s *SmartContract) RegistrarConclusao(ctx contractapi.TransactionContextInterface, idReq string, droneID string, baseID string, setorID string, timestamp string, tipoOcorrencia string, criticidade string, idEmpresa string, custoStr string) (*RespostaTransacao, error) {
	
	// 1. Prevenção de Duplo Gasto Dinâmica (Idempotência)
	// Como a chave do estado é o ID da requisição, se o laudo já existir, a transação cai aqui.
	laudoExistente, err := ctx.GetStub().GetState(idReq)
	if err != nil {
		return nil, fmt.Errorf("falha ao verificar o ledger: %v", err)
	}
	if laudoExistente != nil {
		return &RespostaTransacao{Sucesso: false, Motivo: "erro: esta missao ja foi finalizada e paga anteriormente"}, nil
	}

	custo, err := strconv.Atoi(custoStr)
	if err != nil || custo < 0 {
		return nil, fmt.Errorf("custo operacional invalido: %v", err)
	}

	// 2. Processamento do Débito Financeiro
	carteiraJSON, err := ctx.GetStub().GetState(idEmpresa)
	if err != nil || carteiraJSON == nil {
		return &RespostaTransacao{Sucesso: false, Motivo: "carteira da empresa nao encontrada para faturamento"}, nil
	}

	var carteira Carteira
	json.Unmarshal(carteiraJSON, &carteira)

	if carteira.Saldo < custo {
		return &RespostaTransacao{Sucesso: false, Motivo: "falha critica: saldo insuficiente para liquidação da divida"}, nil
	}

	// Deduz os tokens apenas após a confirmação de que o serviço foi prestado
	carteira.Saldo -= custo
	carteiraAtualizadaJSON, _ := json.Marshal(carteira)
	err = ctx.GetStub().PutState(idEmpresa, carteiraAtualizadaJSON)
	if err != nil {
		return nil, fmt.Errorf("falha ao atualizar fundos da empresa: %v", err)
	}

	// 3. Gravação do Laudo Técnico com os Resultados Reais
	laudo := LaudoMissao{
		IDRequisicao:   idReq,
		DroneID:        droneID,
		BaseID:         baseID,
		SetorID:        setorID,
		Timestamp:      timestamp,
		TipoOcorrencia: tipoOcorrencia,
		Criticidade:    criticidade,
	}

	laudoJSON, _ := json.Marshal(laudo)
	err = ctx.GetStub().PutState(idReq, laudoJSON)
	if err != nil {
		return nil, fmt.Errorf("falha ao salvar o laudo imutavel: %v", err)
	}

	return &RespostaTransacao{Sucesso: true, Motivo: "OK", SaldoRestante: carteira.Saldo}, nil
}

// ── 3. TRANSPARÊNCIA E AUDITABILIDADE ──────────────────────────────────────

// AuditoriaEmpresa busca todo o histórico de alterações no saldo da empresa
func (s *SmartContract) AuditoriaEmpresa(ctx contractapi.TransactionContextInterface, idEmpresa string) (string, error) {
	iterador, err := ctx.GetStub().GetHistoryForKey(idEmpresa)
	if err != nil {
		return "", fmt.Errorf("falha ao buscar historico: %v", err)
	}
	defer iterador.Close()

	var transacoes []HistoricoTransacao

	for iterador.HasNext() {
		resposta, err := iterador.Next()
		if err != nil {
			return "", err
		}

		var carteiraMomento Carteira
		if len(resposta.Value) > 0 {
			json.Unmarshal(resposta.Value, &carteiraMomento)
		}

		txTimestamp := time.Unix(resposta.Timestamp.Seconds, int64(resposta.Timestamp.Nanos)).Format(time.RFC3339)

		tx := HistoricoTransacao{
			TxID:      resposta.TxId,
			Timestamp: txTimestamp,
			Valor:     carteiraMomento.Saldo,
		}
		transacoes = append(transacoes, tx)
	}

	resultado := map[string][]HistoricoTransacao{
		"transacoes": transacoes,
	}

	resultadoJSON, _ := json.Marshal(resultado)
	return string(resultadoJSON), nil
}

// ── PONTO DE ENTRADA ───────────────────────────────────────────────────────

func main() {
	chaincode, err := contractapi.NewChaincode(&SmartContract{})
	if err != nil {
		fmt.Printf("Erro ao criar o chaincode ormuz: %s", err.Error())
		return
	}

	if err := chaincode.Start(); err != nil {
		fmt.Printf("Erro ao iniciar o chaincode: %s", err.Error())
	}
}