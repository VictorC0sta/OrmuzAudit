import uuid
from dataclasses import dataclass, field
from typing import Optional
from constantes import TipoMensagem

# =====================================================================
#  Classes de Mensagens (Modelos prontos para virar JSON e ir para a rede)
# =====================================================================

@dataclass
class MensagemAlerta:
    """O que o Sensor envia para o Setor quando detecta um problema no mar."""
    setor_id: str
    criticidade: str
    tipo_ocorrencia: str
    empresa_id: str         
    # Cria um código único e aleatório para este alerta automaticamente
    id_alerta: str = field(default_factory=lambda: str(uuid.uuid4()))
    tipo: str = TipoMensagem.ALERTA.value

@dataclass
class MensagemRequisicao:
    """O pedido oficial que o Setor espalha para todas as Bases resolverem."""
    id_setor: str
    timestamp_logico: int  # O número do relógio (Lamport) para desempatar quem chegou primeiro
    criticidade: str
    tipo_ocorrencia: str
    # Cria um ID único para a missão não ser confundida ou duplicada pelas bases
    id_requisicao: str = field(default_factory=lambda: str(uuid.uuid4()))
    tipo: str = TipoMensagem.REQUISICAO.value

@dataclass
class MensagemRegistro:
    """O 'aviso de que ligou' que o Drone manda para sua Base ao iniciar."""
    drone_id: str
    base_id: str
    porta: int  # A porta de rede onde o drone vai ficar escutando as ordens
    tipo: str = TipoMensagem.REGISTRO.value

@dataclass
class MensagemHeartbeat:
    """
    Aviso de status do Drone. 
    Enviado o tempo todo para dizer que está vivo, ou no final para avisar que acabou a missão.
    """
    drone_id: str
    base_id: str
    estado: str  # LIVRE ou OCUPADO
    id_requisicao: Optional[str] = None     # Qual missão ele está fazendo agora (se houver)
    missao_concluida: Optional[str] = None  # Qual missão ele acabou de terminar (se houver)
    tipo: str = TipoMensagem.HEARTBEAT.value