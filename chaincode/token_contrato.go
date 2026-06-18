package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/hyperledger/fabric-chaincode-go/shim"
	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

type SmartContract struct {
	contractapi.Contract
}

type Carteira struct {
	IDEmpresa string `json:"id_empresa"`
	Saldo     int    `json:"saldo"`
	MSPID     string `json:"msp_id"`
}

type AutorizacaoPagamento struct {
	IDRequisicao string `json:"id_requisicao"`
	IDEmpresa    string `json:"id_empresa"`
	Custo        int    `json:"custo"`
	Status       string `json:"status"`
}

type LaudoMissao struct {
	IDRequisicao   string `json:"id_requisicao"`
	DroneID        string `json:"drone_id"`
	BaseID         string `json:"base_id"`
	SetorID        string `json:"setor_id"`
	Timestamp      string `json:"timestamp"`
	TipoOcorrencia string `json:"tipo_ocorrencia"`
	Criticidade    string `json:"criticidade"`
}

type RespostaTransacao struct {
	Sucesso          bool   `json:"sucesso"`
	Motivo           string `json:"motivo"`
	SaldoRestante    int    `json:"saldo_restante,omitempty"`
	TransacaoInedita bool   `json:"transacao_inedita"` 
}

type HistoricoTransacao struct {
	TxID      string `json:"tx_id"`
	Timestamp string `json:"timestamp"`
	Valor     int    `json:"saldo_momento"`
}

func (s *SmartContract) CriarCarteira(ctx contractapi.TransactionContextInterface, idEmpresa string, saldoInicialStr string) (*RespostaTransacao, error) {
	carteiraJSON, err := ctx.GetStub().GetState(idEmpresa)
	if err != nil { return nil, fmt.Errorf("falha ao ler o ledger: %v", err) }
	if carteiraJSON != nil { return &RespostaTransacao{Sucesso: false, Motivo: "carteira ja existe"}, nil }

	saldoInicial, err := strconv.Atoi(saldoInicialStr)
	if err != nil { return nil, fmt.Errorf("saldo invalido: %v", err) }

	clientMSPID, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil { return nil, fmt.Errorf("falha ao obter MSPID: %v", err) }

	carteira := Carteira{IDEmpresa: idEmpresa, Saldo: saldoInicial, MSPID: clientMSPID}
	novaCarteiraJSON, _ := json.Marshal(carteira)
	if err = ctx.GetStub().PutState(idEmpresa, novaCarteiraJSON); err != nil { return nil, err }

	return &RespostaTransacao{Sucesso: true, Motivo: "OK", SaldoRestante: saldoInicial}, nil
}

func (s *SmartContract) ConsultarSaldo(ctx contractapi.TransactionContextInterface, idEmpresa string) (*Carteira, error) {
	carteiraJSON, err := ctx.GetStub().GetState(idEmpresa)
	if err != nil { return nil, err }
	if carteiraJSON == nil { return nil, fmt.Errorf("carteira nao existe") }

	var carteira Carteira
	json.Unmarshal(carteiraJSON, &carteira)
	return &carteira, nil
}

func (s *SmartContract) TransferirTokens(ctx contractapi.TransactionContextInterface, idOrigem string, idDestino string, valorStr string) (*RespostaTransacao, error) {
	valor, err := strconv.Atoi(valorStr)
	if err != nil || valor <= 0 { return nil, fmt.Errorf("valor invalido") }

	origemJSON, err := ctx.GetStub().GetState(idOrigem)
	if err != nil || origemJSON == nil { return &RespostaTransacao{Sucesso: false, Motivo: "origem nao encontrada"}, nil }

	var carteiraOrigem Carteira
	json.Unmarshal(origemJSON, &carteiraOrigem)

	clientMSPID, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil { return nil, err }
	if carteiraOrigem.MSPID != clientMSPID { return &RespostaTransacao{Sucesso: false, Motivo: "assinatura invalida"}, nil }

	if carteiraOrigem.Saldo < valor { return &RespostaTransacao{Sucesso: false, Motivo: "saldo insuficiente"}, nil }

	destinoJSON, err := ctx.GetStub().GetState(idDestino)
	if err != nil || destinoJSON == nil { return &RespostaTransacao{Sucesso: false, Motivo: "destino nao encontrado"}, nil }

	var carteiraDestino Carteira
	json.Unmarshal(destinoJSON, &carteiraDestino)

	carteiraOrigem.Saldo -= valor
	carteiraDestino.Saldo += valor

	novaOrigemJSON, _ := json.Marshal(carteiraOrigem)
	novoDestinoJSON, _ := json.Marshal(carteiraDestino)

	ctx.GetStub().PutState(idOrigem, novaOrigemJSON)
	ctx.GetStub().PutState(idDestino, novoDestinoJSON)

	return &RespostaTransacao{Sucesso: true, Motivo: "Transferencia concluida", SaldoRestante: carteiraOrigem.Saldo}, nil
}

func (s *SmartContract) AutorizarPagamento(ctx contractapi.TransactionContextInterface, idRequisicao string, idEmpresa string, custoStr string) (*RespostaTransacao, error) {
	chavePagamento := "PAG_" + idRequisicao
	pagamentoExistente, err := ctx.GetStub().GetState(chavePagamento)
	if err != nil { return nil, err }
	if pagamentoExistente != nil {
		return &RespostaTransacao{Sucesso: true, Motivo: "pagamento ja autorizado (idempotente)", TransacaoInedita: false}, nil
	}

	custo, err := strconv.Atoi(custoStr)
	if err != nil || custo < 0 { return nil, err }

	carteiraJSON, err := ctx.GetStub().GetState(idEmpresa)
	if err != nil || carteiraJSON == nil { return &RespostaTransacao{Sucesso: false, Motivo: "carteira nao encontrada"}, nil }

	var carteira Carteira
	json.Unmarshal(carteiraJSON, &carteira)

	if carteira.Saldo < custo { return &RespostaTransacao{Sucesso: false, Motivo: "saldo insuficiente"}, nil }

	carteira.Saldo -= custo
	carteiraAtualizadaJSON, _ := json.Marshal(carteira)
	ctx.GetStub().PutState(idEmpresa, carteiraAtualizadaJSON)

	autorizacao := AutorizacaoPagamento{IDRequisicao: idRequisicao, IDEmpresa: idEmpresa, Custo: custo, Status: "AUTORIZADO"}
	autorizacaoJSON, _ := json.Marshal(autorizacao)
	ctx.GetStub().PutState(chavePagamento, autorizacaoJSON)

	return &RespostaTransacao{Sucesso: true, Motivo: "OK", SaldoRestante: carteira.Saldo, TransacaoInedita: true}, nil
}

func (s *SmartContract) RegistrarLaudo(ctx contractapi.TransactionContextInterface, idReq string, droneID string, baseID string, setorID string, timestamp string, tipoOcorrencia string, criticidade string) (*RespostaTransacao, error) {
	laudoExistente, err := ctx.GetStub().GetState(idReq)
	if err != nil { return nil, err }
	if laudoExistente != nil { return &RespostaTransacao{Sucesso: false, Motivo: "laudo ja registrado"}, nil }

	pagamentoJSON, err := ctx.GetStub().GetState("PAG_" + idReq)
	if err != nil { return nil, err }
	if pagamentoJSON == nil { return &RespostaTransacao{Sucesso: false, Motivo: "pagamento nao autorizado"}, nil }

	laudo := LaudoMissao{IDRequisicao: idReq, DroneID: droneID, BaseID: baseID, SetorID: setorID, Timestamp: timestamp, TipoOcorrencia: tipoOcorrencia, Criticidade: criticidade}
	laudoJSON, _ := json.Marshal(laudo)
	ctx.GetStub().PutState(idReq, laudoJSON)

	return &RespostaTransacao{Sucesso: true, Motivo: "OK"}, nil
}

func (s *SmartContract) AuditoriaEmpresa(ctx contractapi.TransactionContextInterface, idEmpresa string) (string, error) {
	iterador, err := ctx.GetStub().GetHistoryForKey(idEmpresa)
	if err != nil { return "", err }
	defer iterador.Close()

	var transacoes []HistoricoTransacao
	for iterador.HasNext() {
		resposta, _ := iterador.Next()
		var carteiraMomento Carteira
		if len(resposta.Value) > 0 { json.Unmarshal(resposta.Value, &carteiraMomento) }
		txTimestamp := time.Unix(resposta.Timestamp.Seconds, int64(resposta.Timestamp.Nanos)).Format(time.RFC3339)
		transacoes = append(transacoes, HistoricoTransacao{TxID: resposta.TxId, Timestamp: txTimestamp, Valor: carteiraMomento.Saldo})
	}
	resultado := map[string][]HistoricoTransacao{"transacoes": transacoes}
	resultadoJSON, _ := json.Marshal(resultado)
	return string(resultadoJSON), nil
}

func main() {
	chaincode, err := contractapi.NewChaincode(&SmartContract{})
	if err != nil {
		fmt.Printf("Erro ao criar chaincode: %s", err.Error())
		return
	}

	// NOVO: Arranque do servidor CCaaS em vez do build interno!
	server := &shim.ChaincodeServer{
		CCID:    os.Getenv("CHAINCODE_ID"),
		Address: os.Getenv("CHAINCODE_SERVER_ADDRESS"),
		CC:      chaincode,
		TLSProps: shim.TLSProperties{
			Disabled: true, // Sem TLS para simplificar a rede interna
		},
	}

	fmt.Printf("Iniciando Chaincode-as-a-Service em %s (CCID: %s)\n", os.Getenv("CHAINCODE_SERVER_ADDRESS"), os.Getenv("CHAINCODE_ID"))
	if err := server.Start(); err != nil {
		fmt.Printf("Erro no servidor CCaaS: %s", err.Error())
	}
}
