import threading
import sys
import os

# Ajusta o path para importar os modulos compartilhados e da base
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'shared')))

from base.fila_replicada import FilaReplicada, EntradaFila

def test_exclusao_mutua_concorrente():
    print("Iniciando teste de consistencia: Concorrencia Extrema de Aceite...")
    fila = FilaReplicada()
    
    # Injeta uma requisicao simulada no estado pendente
    req = EntradaFila(
        id_requisicao="REQ-TESTE-CARGA-001",
        id_setor="S1",
        timestamp_logico=1,
        criticidade="CRITICA",
        tipo_ocorrencia="embarcacao_perigo"
    )
    fila.inserir_na_fila(req)

    resultados_aceite = []
    
    # Funcao que 20 threads vao rodar ao mesmo tempo
    def tentar_aceitar():
        # O metodo marcar_aceita usa o fila_lock internamente
        if fila.marcar_aceita("REQ-TESTE-CARGA-001"):
            resultados_aceite.append(1)

    threads = []
    for _ in range(20):
        t = threading.Thread(target=tentar_aceitar)
        threads.append(t)

    # Dispara todas as threads simultaneamente
    for t in threads:
        t.start()
        
    for t in threads:
        t.join()

    # O teste so passa se exatamente 1 thread conseguiu aceitar
    assert len(resultados_aceite) == 1, f"FALHA: {len(resultados_aceite)} aceites para a mesma requisicao!"
    print(f"SUCESSO: Apenas 1 aceite registrado entre {len(threads)} tentativas simultaneas.")
    print("Consistencia e Exclusao Mutua local garantidas sob carga.")

if __name__ == "__main__":
    test_exclusao_mutua_concorrente()