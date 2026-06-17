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
}

// LaudoMissao guarda o registro imutável do que o drone fez
type LaudoMissao struct {
    IDRequisicao   string `json:"id_requisicao"`
    DroneID        string `json:"drone_id"`
    BaseID         string `json:"base_id"`
    SetorID        string `json:"setor_id"`
    Timestamp      string `json:"timestamp"`
    TipoOcorrencia string `json:"tipo_ocorrencia"` // NOVO CAMPO
    Criticidade    string `json:"criticidade"`     // NOVO CAMPO
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

// ── 1. GESTÃO DE ATIVOS (CRÉDITOS E PREVENÇÃO DE DUPLO GASTO) ──────────────

// CriarCarteira inicializa uma empresa com seus créditos operacionais
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

    carteira := Carteira{IDEmpresa: idEmpresa, Saldo: saldoInicial}
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

// DebitarTokens processa o pagamento da escolta (Evita Duplo Gasto via MVCC)
func (s *SmartContract) DebitarTokens(ctx contractapi.TransactionContextInterface, idEmpresa string, valorStr string, idReq string) (*RespostaTransacao, error) {
    valor, err := strconv.Atoi(valorStr)
    if err != nil {
        return nil, fmt.Errorf("valor invalido: %v", err)
    }

    carteiraJSON, err := ctx.GetStub().GetState(idEmpresa)
    if err != nil || carteiraJSON == nil {
        return &RespostaTransacao{Sucesso: false, Motivo: "carteira nao encontrada"}, nil
    }

    var carteira Carteira
    json.Unmarshal(carteiraJSON, &carteira)

    if carteira.Saldo < valor {
        return &RespostaTransacao{Sucesso: false, Motivo: "saldo insuficiente"}, nil
    }

    carteira.Saldo -= valor
    carteiraAtualizadaJSON, _ := json.Marshal(carteira)

    err = ctx.GetStub().PutState(idEmpresa, carteiraAtualizadaJSON)
    if err != nil {
        return nil, fmt.Errorf("falha ao salvar no ledger: %v", err)
    }

    return &RespostaTransacao{Sucesso: true, Motivo: "OK", SaldoRestante: carteira.Saldo}, nil
}

// TransferirTokens move fundos de uma carteira para outra (Demonstração de transferência entre empresas)
func (s *SmartContract) TransferirTokens(ctx contractapi.TransactionContextInterface, idOrigem string, idDestino string, valorStr string) (*RespostaTransacao, error) {
    valor, err := strconv.Atoi(valorStr)
    if err != nil || valor <= 0 {
        return nil, fmt.Errorf("valor de transferencia invalido: %v", err)
    }

    // 1. Ler e validar carteira de origem
    origemJSON, err := ctx.GetStub().GetState(idOrigem)
    if err != nil || origemJSON == nil {
        return &RespostaTransacao{Sucesso: false, Motivo: "carteira de origem nao encontrada"}, nil
    }

    var carteiraOrigem Carteira
    json.Unmarshal(origemJSON, &carteiraOrigem)

    if carteiraOrigem.Saldo < valor {
        return &RespostaTransacao{Sucesso: false, Motivo: "saldo insuficiente na origem"}, nil
    }

    // 2. Ler e validar carteira de destino
    destinoJSON, err := ctx.GetStub().GetState(idDestino)
    if err != nil || destinoJSON == nil {
        return &RespostaTransacao{Sucesso: false, Motivo: "carteira de destino nao encontrada"}, nil
    }

    var carteiraDestino Carteira
    json.Unmarshal(destinoJSON, &carteiraDestino)

    // 3. Efetuar a transferência em memória
    carteiraOrigem.Saldo -= valor
    carteiraDestino.Saldo += valor

    // 4. Persistir as duas carteiras
    novaOrigemJSON, _ := json.Marshal(carteiraOrigem)
    novoDestinoJSON, _ := json.Marshal(carteiraDestino)

    err = ctx.GetStub().PutState(idOrigem, novaOrigemJSON)
    if err != nil {
        return nil, fmt.Errorf("falha ao debitar origem: %v", err)
    }

    err = ctx.GetStub().PutState(idDestino, novoDestinoJSON)
    if err != nil {
        return nil, fmt.Errorf("falha ao creditar destino: %v", err)
    }

    return &RespostaTransacao{Sucesso: true, Motivo: "Transferencia concluida com sucesso", SaldoRestante: carteiraOrigem.Saldo}, nil
}

// ── 2. LOG DE OPERAÇÕES IMUTÁVEL ───────────────────────────────────────────

// RegistrarConclusao cria o "Laudo" da missão, vinculando o gasto ao resultado final
func (s *SmartContract) RegistrarConclusao(ctx contractapi.TransactionContextInterface, idReq string, droneID string, baseID string, setorID string, timestamp string, tipoOcorrencia string, criticidade string) (*RespostaTransacao, error) {
    // Verifica se a missão já tem laudo
    laudoExistente, err := ctx.GetStub().GetState(idReq)
    if err != nil {
        return nil, fmt.Errorf("falha ao verificar o ledger: %v", err)
    }
    if laudoExistente != nil {
        return &RespostaTransacao{Sucesso: false, Motivo: "laudo ja registrado para esta missao"}, nil
    }

    laudo := LaudoMissao{
        IDRequisicao:   idReq,
        DroneID:        droneID,
        BaseID:         baseID,
        SetorID:        setorID,
        Timestamp:      timestamp,
        TipoOcorrencia: tipoOcorrencia, // PREENCHIDO
        Criticidade:    criticidade,    // PREENCHIDO
    }

    laudoJSON, _ := json.Marshal(laudo)

    // O ID da requisição vira a "Chave Primária" do laudo no Ledger
    err = ctx.GetStub().PutState(idReq, laudoJSON)
    if err != nil {
        return nil, fmt.Errorf("falha ao salvar o laudo: %v", err)
    }

    return &RespostaTransacao{Sucesso: true, Motivo: "OK"}, nil
}

// ── 3. TRANSPARÊNCIA E AUDITABILIDADE ──────────────────────────────────────

// AuditoriaEmpresa busca todo o histórico de alterações no saldo da empresa
func (s *SmartContract) AuditoriaEmpresa(ctx contractapi.TransactionContextInterface, idEmpresa string, pdcName string) (string, error) {
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