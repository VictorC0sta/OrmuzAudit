package main

import (
	"encoding/json"
	"fmt"
	"strconv"
	"time"

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

// AutorizacaoPagamento é o registro imutável de que o débito de uma missão
// já foi feito ANTES do drone ser despachado. A chave no ledger é "PAG_"+IDRequisicao,
// o que torna a autorização idempotente: se a mesma requisição passar por aqui de
// novo (ex.: re-despacho após perda de drone), não cobramos duas vezes.
type AutorizacaoPagamento struct {
	IDRequisicao string `json:"id_requisicao"`
	IDEmpresa    string `json:"id_empresa"`
	Custo        int    `json:"custo"`
	Status       string `json:"status"` // AUTORIZADO
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

// ── 2. PAGAMENTO ANTES DO DESPACHO + LAUDO IMUTÁVEL DEPOIS DA MISSÃO ────────
//
// Antes: o débito acontecia só em RegistrarConclusao, ou seja, DEPOIS do drone
// já ter feito a missão inteira. Isso violava a regra "drone só é despachado
// após confirmação do pagamento" e abria brecha para múltiplas requisições da
// mesma empresa serem aceitas e despachadas mesmo sem saldo suficiente para
// todas (só a 1ª conclusão conseguia debitar; as demais "trabalhavam de
// graça"). Agora dividimos em duas transações atômicas:
//
//   1) AutorizarPagamento — chamada pela Base ANTES de despachar o drone.
//      Debita a carteira e grava um recibo imutável em "PAG_"+idRequisicao.
//      É idempotente: se a mesma requisição passar aqui de novo (ex.: drone
//      caiu e a missão foi reemitida para outra base), não cobra de novo.
//
//   2) RegistrarLaudo — chamada quando a missão termina. Só grava o laudo se
//      já existir uma autorização de pagamento para aquela requisição —
//      nunca debita nada.

// AutorizarPagamento debita o custo da missão da carteira da empresa ANTES
// do drone ser despachado. Retorna sucesso=false se não houver saldo.
func (s *SmartContract) AutorizarPagamento(ctx contractapi.TransactionContextInterface, idRequisicao string, idEmpresa string, custoStr string) (*RespostaTransacao, error) {
	chavePagamento := "PAG_" + idRequisicao

	pagamentoExistente, err := ctx.GetStub().GetState(chavePagamento)
	if err != nil {
		return nil, fmt.Errorf("falha ao verificar o ledger: %v", err)
	}
	if pagamentoExistente != nil {
		// Idempotência: já autorizado antes (ex.: redespacho após drone perdido).
		// Não cobra de novo — apenas confirma que o pagamento já está garantido.
		return &RespostaTransacao{Sucesso: true, Motivo: "pagamento ja autorizado anteriormente (idempotente)"}, nil
	}

	custo, err := strconv.Atoi(custoStr)
	if err != nil || custo < 0 {
		return nil, fmt.Errorf("custo operacional invalido: %v", err)
	}

	carteiraJSON, err := ctx.GetStub().GetState(idEmpresa)
	if err != nil || carteiraJSON == nil {
		return &RespostaTransacao{Sucesso: false, Motivo: "carteira da empresa nao encontrada para autorizacao"}, nil
	}

	var carteira Carteira
	json.Unmarshal(carteiraJSON, &carteira)

	if carteira.Saldo < custo {
		return &RespostaTransacao{Sucesso: false, Motivo: "saldo insuficiente para autorizar a missao"}, nil
	}

	// Débito imediato. Se duas requisições concorrentes da mesma empresa
	// chegarem aqui ao mesmo tempo, o controle de versão do Fabric (MVCC)
	// garante que apenas uma das transações que escrevem na mesma chave
	// "idEmpresa" será validada no bloco — a outra é invalidada pelo próprio
	// consenso, e não apenas pela lógica do chaincode.
	carteira.Saldo -= custo
	carteiraAtualizadaJSON, _ := json.Marshal(carteira)
	if err := ctx.GetStub().PutState(idEmpresa, carteiraAtualizadaJSON); err != nil {
		return nil, fmt.Errorf("falha ao debitar carteira: %v", err)
	}

	autorizacao := AutorizacaoPagamento{
		IDRequisicao: idRequisicao,
		IDEmpresa:    idEmpresa,
		Custo:        custo,
		Status:       "AUTORIZADO",
	}
	autorizacaoJSON, _ := json.Marshal(autorizacao)
	if err := ctx.GetStub().PutState(chavePagamento, autorizacaoJSON); err != nil {
		return nil, fmt.Errorf("falha ao registrar autorizacao de pagamento: %v", err)
	}

	return &RespostaTransacao{Sucesso: true, Motivo: "OK", SaldoRestante: carteira.Saldo}, nil
}

// RegistrarLaudo grava o resultado da missão de forma imutável. Não move
// nenhum token — o pagamento já foi feito em AutorizarPagamento, antes do
// despacho. Recusa registrar laudo de missão sem pagamento autorizado.
func (s *SmartContract) RegistrarLaudo(ctx contractapi.TransactionContextInterface, idReq string, droneID string, baseID string, setorID string, timestamp string, tipoOcorrencia string, criticidade string) (*RespostaTransacao, error) {
	laudoExistente, err := ctx.GetStub().GetState(idReq)
	if err != nil {
		return nil, fmt.Errorf("falha ao verificar o ledger: %v", err)
	}
	if laudoExistente != nil {
		return &RespostaTransacao{Sucesso: false, Motivo: "laudo ja registrado para esta missao"}, nil
	}

	chavePagamento := "PAG_" + idReq
	pagamentoJSON, err := ctx.GetStub().GetState(chavePagamento)
	if err != nil {
		return nil, fmt.Errorf("falha ao verificar pagamento: %v", err)
	}
	if pagamentoJSON == nil {
		return &RespostaTransacao{Sucesso: false, Motivo: "nenhum pagamento autorizado para esta requisicao — laudo rejeitado"}, nil
	}

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
	if err := ctx.GetStub().PutState(idReq, laudoJSON); err != nil {
		return nil, fmt.Errorf("falha ao salvar o laudo imutavel: %v", err)
	}

	return &RespostaTransacao{Sucesso: true, Motivo: "OK"}, nil
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