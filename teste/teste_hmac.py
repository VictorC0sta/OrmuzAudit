"""
teste_hmac.py — Prova que a autenticação dos sensores (shared/auth.py) funciona.

Executa sem rede e sem Docker — só testa a lógica de assinatura/verificação.
Roda a partir da raiz do projeto: python teste/teste_hmac.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shared")))

# pylint: disable=import-error, wrong-import-position
from auth import assinar, verificar


def test_assinatura_valida_passa():
    sig = assinar("S1", "embarcacao_perigo", "CRITICA", "EMPRESA-A", "abc-123")
    assert verificar("S1", "embarcacao_perigo", "CRITICA", "EMPRESA-A", "abc-123", sig)
    print("OK — assinatura valida foi aceita")


def test_empresa_forjada_falha():
    # O sensor da EMPRESA-A assina, mas alguem tenta reenviar dizendo ser a EMPRESA-B
    sig = assinar("S1", "embarcacao_perigo", "CRITICA", "EMPRESA-A", "abc-123")
    assert not verificar("S1", "embarcacao_perigo", "CRITICA", "EMPRESA-B", "abc-123", sig)
    print("OK — empresa_id forjado foi rejeitado")


def test_payload_alterado_falha():
    sig = assinar("S1", "embarcacao_perigo", "CRITICA", "EMPRESA-A", "abc-123")
    # Tenta trocar a criticidade depois de assinado, pra pagar menos (CRITICA -> BAIXA)
    assert not verificar("S1", "embarcacao_perigo", "BAIXA", "EMPRESA-A", "abc-123", sig)
    print("OK — payload alterado apos a assinatura foi rejeitado")


def test_sem_assinatura_falha():
    assert not verificar("S1", "embarcacao_perigo", "CRITICA", "EMPRESA-A", "abc-123", None)
    print("OK — alerta sem assinatura foi rejeitado")


def test_empresa_desconhecida_falha():
    sig = assinar("S1", "embarcacao_perigo", "CRITICA", "EMPRESA-A", "abc-123")
    assert not verificar("S1", "embarcacao_perigo", "CRITICA", "EMPRESA-INEXISTENTE", "abc-123", sig)
    print("OK — empresa sem segredo cadastrado foi rejeitada")


if __name__ == "__main__":
    test_assinatura_valida_passa()
    test_empresa_forjada_falha()
    test_payload_alterado_falha()
    test_sem_assinatura_falha()
    test_empresa_desconhecida_falha()
    print("\nTODOS OS TESTES DE HMAC PASSARAM.")