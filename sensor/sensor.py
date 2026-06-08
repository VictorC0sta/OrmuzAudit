"""
sensor.py — Componente Sensor do sistema Ormuz Command Center.

Papel na arquitetura:
    O sensor atua como o publicador (produtor) de dados brutos na malha.
    Ele simula a detecção de anomalias marítimas em um setor específico do 
    Estreito de Ormuz. Opera de forma totalmente autônoma, gerando eventos
    aleatórios e enviando-os via TCP para o Broker do seu respectivo setor.
"""

import os 
import time
import random
import logging
from dataclasses import asdict
import sys

# Adiciona a pasta "shared" (um nível acima) no path para importar módulos comuns
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

# pylint: disable=import-error, wrong-import-position
from constantes import TipoOcorrencia, CRITICIDADE_POR_TIPO
from mensagens import MensagemAlerta
from protocolo import tcp_enviar

# ── Configuração de Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("Sensor")


# ── Configurações via Variáveis de Ambiente ───────────────────────────────────
# Identificação
SENSOR_ID = os.environ.get("SENSOR_ID", "SENSOR-S1")
SETOR_ID  = os.environ.get("SETOR_ID", "S1")

# Comunicação (Alvo: Broker do Setor)
IP_BROKER_SETOR = os.environ.get("IP_BROKER_SETOR", "127.0.0.1")
PORTA_BROKER_SETOR = int(os.environ.get("PORTA_BROKER_SETOR", 5050))

# Frequência de geração de alertas (em segundos)
INTERVALO_MIN = float(os.environ.get("INTERVALO_MIN", 5))
INTERVALO_MAX = float(os.environ.get("INTERVALO_MAX", 15))

# Informações empresariais 
EMPRESA_ID = os.environ.get("EMPRESA_ID", "EMPRESA-A")
CARTEIRA_ID = os.environ.get("CARTEIRA_ID", "wallet-abc123")


# ── Distribuição de Probabilidade ─────────────────────────────────────────────
# Simula a realidade: eventos críticos são raros, eventos menores são frequentes.
# A tupla contém (Tipo_da_Ocorrencia, Peso_no_Sorteio).
TIPOS_E_PESOS = [
    (TipoOcorrencia.EMBARCACAO_PERIGO,       3),   # Muito raro
    (TipoOcorrencia.BLOQUEIO_ROTA,           7),   # Raro
    (TipoOcorrencia.OBJETO_NAO_IDENTIFICADO, 10),  # Incomum
    (TipoOcorrencia.EMBARCACAO_DERIVA,       20),  # Ocasional
    (TipoOcorrencia.FALHA_SINALIZACAO,       25),  # Comum
    (TipoOcorrencia.ANOMALIA_MENOR,          35),  # Muito comum
]

# Separa as listas para uso na função random.choices
_tipos = [tipo for tipo, peso in TIPOS_E_PESOS]
_pesos = [peso for tipo, peso in TIPOS_E_PESOS]


def gerar_ocorrencia() -> MensagemAlerta:
    """
    Sorteia um evento baseado nos pesos definidos e cria o objeto de alerta.
    Busca a criticidade correspondente (CRITICA, ALTA, BAIXA) no dicionário
    compartilhado em constantes.py.
    """
    # Sorteia 1 tipo usando os pesos (retorna uma lista de 1 elemento, pegamos o índice [0])
    tipo = random.choices(_tipos, weights=_pesos, k=1)[0]
    criticidade = CRITICIDADE_POR_TIPO[tipo]

    return MensagemAlerta(
        setor_id=SETOR_ID,
        tipo_ocorrencia=tipo.value,
        criticidade=criticidade,
    )


def enviar_alerta(alerta: MensagemAlerta) -> bool:
    """
    Converte o objeto dataclass MensagemAlerta para um dicionário (JSON)
    e envia ao broker do setor via TCP usando a função compartilhada.
    
    Retorna True se o envio for bem-sucedido, False caso contrário.
    """
    payload = asdict(alerta)
    sucesso = tcp_enviar(IP_BROKER_SETOR, PORTA_BROKER_SETOR, payload)
 
    if sucesso:
        logger.info("[%s] Alerta enviado → %s | %s [%s] (id: %s...)",
                    SENSOR_ID, SETOR_ID, alerta.tipo_ocorrencia, 
                    alerta.criticidade, alerta.id_alerta[:8])
    else:
        # Falha silenciosa em termos de interrupção (não trava o sensor),
        # pois o sensor de IoT real apenas tenta enviar os dados no momento.
        logger.error("[%s] Falha ao enviar alerta para broker %s:%d — "
                     "broker offline? Tentará novamente no próximo ciclo.",
                     SENSOR_ID, IP_BROKER_SETOR, PORTA_BROKER_SETOR)
    return sucesso


def main():
    """
    Loop infinito que simula o funcionamento contínuo do sensor.
    Gera um alerta, envia para o broker, e dorme por um tempo aleatório
    antes de repetir o ciclo.
    """
    logger.info("[%s] Iniciando — setor %s | broker em %s:%d | intervalo %.1f–%.1fs",
                SENSOR_ID, SETOR_ID, IP_BROKER_SETOR, PORTA_BROKER_SETOR, 
                INTERVALO_MIN, INTERVALO_MAX)
 
    # Atraso inicial aleatório para que todos os sensores (S1 a S8) 
    # não disparem alertas exatamente no mesmo milissegundo ao iniciar os contêineres
    tempo_inicial = random.uniform(2, 8)
    logger.info("[%s] Aguardando %.1fs antes do primeiro alerta...", SENSOR_ID, tempo_inicial)
    time.sleep(tempo_inicial)
 
    while True:
        # Cria a anomalia
        alerta = gerar_ocorrencia()
        
        # Envia para a rede
        enviar_alerta(alerta)
 
        # Sorteia quanto tempo a simulação deve "dormir" até o próximo evento
        intervalo = random.uniform(INTERVALO_MIN, INTERVALO_MAX)
        logger.debug("[%s] Próxima ocorrência em %.1fs", SENSOR_ID, intervalo)
        time.sleep(intervalo)
 
if __name__ == "__main__":
    main()