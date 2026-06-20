
import os
import random
import logging
from dataclasses import asdict
import sys
import time
import threading

# Adiciona a pasta "shared" (um nível acima) no path para importar módulos comuns
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

# pylint: disable=import-error, wrong-import-position
from constantes import EstadoDrone, TipoMensagem, HEARTBEAT_INTERVALO_S
from mensagens import MensagemRegistro, MensagemHeartbeat
from protocolo import (
    tcp_enviar,
    tcp_receber_completo,
    criar_servidor_tcp,
    udp_enviar
)

#Configuração de Logging 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("drone")

#Configurações via Variáveis de Ambiente
# Identificação do drone e sua base controladora
DRONE_ID = os.environ.get("DRONE_ID", "DRONE-NORTE-1")
BASE_ORIGEM = os.environ.get("BASE_ORIGEM", "NORTE")
IP_BASE = os.environ.get("IP_BASE", "127.0.0.1")

# Portas de comunicação
PORTA_BASE_TCP = int(os.environ.get("PORTA_BASE_TCP", "6001"))  # Onde a base escuta TCP
PORTA_BASE_UDP = int(os.environ.get("PORTA_BASE_UDP", "6101"))  # Onde a base escuta UDP
PORTA_HEARTBEAT = int(os.environ.get("PORTA_HEARTBEAT", "7001")) # Onde o drone escuta TCP

# Tempos e limites
HEARTBEAT_INTERVALO = float(os.environ.get("HEARTBEAT_INTERVALO", str(HEARTBEAT_INTERVALO_S)))
MISSAO_DURACAO_MIN = float(os.environ.get("MISSAO_DURACAO_MIN", "8"))
MISSAO_DURACAO_MAX = float(os.environ.get("MISSAO_DURACAO_MAX", "20"))
MAX_TENTATIVAS = int(os.environ.get("MAX_TENTATIVAS", "5"))


#Controle de Estado Local 
class Drone:
    def __init__(self):
        self.estado = EstadoDrone.LIVRE
        self.id_requisicao_atual = None
        self._lock = threading.Lock()

    def ocupar(self, id_requisicao):
        """Muda o estado para OCUPADO e vincula ao ID da missão."""
        with self._lock:
            self.estado = EstadoDrone.OCUPADO
            self.id_requisicao_atual = id_requisicao

    def liberar(self):
        """Limpa a missão atual e volta para o estado LIVRE."""
        with self._lock:
            self.estado = EstadoDrone.LIVRE
            self.id_requisicao_atual = None

    @property
    def livre(self):
        """Retorna True se o drone estiver disponível para missões."""
        with self._lock:
            return self.estado == EstadoDrone.LIVRE

# Instância global que guarda o estado deste drone
drone = Drone()


#Funções de Comunicação e Negócio 

def registrar_na_base():
    msg = MensagemRegistro(
        drone_id=DRONE_ID,
        base_id=BASE_ORIGEM,
        porta=PORTA_HEARTBEAT
    )

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        logger.info("[%s] Tentando registrar (tentativa %d/%d)", 
                    DRONE_ID, tentativa, MAX_TENTATIVAS)

        # Se o envio TCP der certo, o registro foi concluído
        if tcp_enviar(IP_BASE, PORTA_BASE_TCP, asdict(msg)):
            logger.info("[%s] Registro realizado com sucesso", DRONE_ID)
            return

        # Se falhou, calcula tempo de espera (2s, 4s, 8s...) travado em no máximo 30s
        espera = min(2 ** tentativa, 30)
        logger.warning("[%s] Falha. Nova tentativa em %ds", DRONE_ID, espera)
        time.sleep(espera)

    # Se esgotar as tentativas, mata o processo do drone
    logger.error("[%s] Falha após %d tentativas", DRONE_ID, MAX_TENTATIVAS)
    sys.exit(1)


def enviar_heartbeat():
    logger.info("[%s] Heartbeat iniciado", DRONE_ID)

    while True:
        msg = MensagemHeartbeat(
            drone_id=DRONE_ID,
            base_id=BASE_ORIGEM,
            estado=drone.estado.value,
            id_requisicao=drone.id_requisicao_atual
        )

        udp_enviar(IP_BASE, PORTA_BASE_UDP, asdict(msg))
        time.sleep(HEARTBEAT_INTERVALO)


def executar_missao(dados_missao):
    id_req = dados_missao.get("id_requisicao", "?")
    setor = dados_missao.get("setor_id", "?")
    tipo = dados_missao.get("tipo_ocorrencia", "?")
    criticidade = dados_missao.get("criticidade", "?")

    logger.info("[%s] Iniciando missão %s | Setor:%s | Tipo:%s [%s]", 
                DRONE_ID, id_req, setor, tipo, criticidade)

    # Bloqueia o drone para não aceitar novas missões
    drone.ocupar(id_req)
    
    # Sorteia um tempo de simulação para a missão e "dorme" por esse tempo
    duracao = random.uniform(MISSAO_DURACAO_MIN, MISSAO_DURACAO_MAX)
    logger.info("[%s] Missão em andamento (%.1fs)", DRONE_ID, duracao)
    time.sleep(duracao)

    # Libera o drone
    drone.liberar()
    logger.info("[%s] Missão concluída", DRONE_ID)

    # Monta a mensagem de conclusão simulando um Heartbeat de confirmação (via TCP)
    conclusao = {
        "tipo": TipoMensagem.HEARTBEAT.value,
        "drone_id": DRONE_ID,
        "base_id": BASE_ORIGEM,
        "estado": drone.estado.value,
        "id_requisicao_atual": None,
        "missao_concluida": id_req
    }

    # Envia o aviso de missão concluída com TCP para garantir que a base receba
    tcp_enviar(IP_BASE, PORTA_BASE_TCP, conclusao)


def loop_servidor_tcp():
    servidor = criar_servidor_tcp(PORTA_HEARTBEAT)
    logger.info("[%s] Servidor TCP iniciado na porta %d", DRONE_ID, PORTA_HEARTBEAT)

    while True:
        try:
            # Aceita conexões que chegam da base
            conn, addr = servidor.accept()
            msg = tcp_receber_completo(conn)
            conn.close()

            if not msg:
                logger.warning("[%s] Sem dados de %s", DRONE_ID, addr)
                continue

            tipo = msg.get("tipo")

            # Se for uma ordem de missão, dispara em thread separada para 
            # não travar o socket TCP do drone enquanto ele "viaja"
            if tipo == "MISSAO":
                if drone.livre:
                    threading.Thread(
                        target=executar_missao,
                        args=(msg,),
                        daemon=True
                    ).start()
                else:
                    # Caso receba missão mas já esteja ocupado (cenário de borda/erro)
                    logger.warning("[%s] Drone ocupado, ignorando missão %s", 
                                   DRONE_ID, msg.get("id_requisicao"))
            else:
                logger.warning("[%s] Tipo de mensagem desconhecido: %s", DRONE_ID, tipo)

        except Exception as e:
            logger.error("[%s] Erro no loop TCP: %s", DRONE_ID, e, exc_info=True)



def main():
    """Inicializa o drone, registra na base, inicia telemetria e o servidor TCP."""
    logger.info("[%s] Inicializando...", DRONE_ID)

    # 1. Avisa a base que está vivo
    registrar_na_base()

    # 2. Inicia o envio de UDP periódico em background
    t_heartbeat = threading.Thread(
        target=enviar_heartbeat,
        daemon=True
    )
    t_heartbeat.start()

    # 3. Trava a thread principal escutando novos chamados TCP da base
    loop_servidor_tcp()


if __name__ == "__main__":
    main()