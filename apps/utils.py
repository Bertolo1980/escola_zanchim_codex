import requests
import re

def enviar_whatsapp(telefone, mensagem):
    """Envia mensagem via API externa (sem Twilio)"""

    if not telefone:
        return None

    try:
        # limpa telefone
        telefone_limpo = re.sub(r'[^0-9]', '', str(telefone))

        if not telefone_limpo.startswith('55'):
            telefone_limpo = '55' + telefone_limpo

        # 🔴 CONFIGURE AQUI (mesma API do outro projeto)
        url = "SUA_URL_API"

        payload = {
            "phone": telefone_limpo,
            "message": mensagem
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": "SEU_TOKEN"
        }

        response = requests.post(url, json=payload, headers=headers)

        print("Resposta WhatsApp:", response.text)

        return response.text

    except Exception as e:
        print("Erro ao enviar WhatsApp:", e)
        return None
