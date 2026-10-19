import html
import json
import re

import requests

from core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from database.database import definir_feedback, definir_metadado, obter_metadado
from core.logger import get_logger

logger = get_logger()


def _html(valor: object) -> str:
    """Escapa dados das vagas antes de montar mensagens HTML do Telegram."""
    return html.escape(str(valor), quote=True)


def enviar_mensagem(texto: str, reply_markup: dict | None = None) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram não configurado (token/chat_id ausentes no .env). Pulando envio.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        resposta = requests.post(url, data=payload, timeout=10)
        resposta.raise_for_status()
        
        # Validação estrita da resposta da API do Telegram (corrige o falso positivo de HTTP 200)
        try:
            dados = resposta.json()
        except ValueError:
            logger.error("Erro ao processar resposta JSON do Telegram: resposta inválida")
            return False

        if not dados.get("ok", False):
            descricao = dados.get("description", "sem descrição")
            logger.error(f"Erro retornado pela API do Telegram: {descricao}")
            return False

        return True

    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        motivo = e.response.reason if e.response is not None else "sem detalhe"
        detalhe = ""
        if e.response is not None:
            try:
                detalhe = e.response.json().get("description", "")
            except ValueError:
                detalhe = ""
        sufixo = f": {detalhe}" if detalhe else ""
        logger.error(f"Erro ao enviar mensagem no Telegram: HTTP {status} ({motivo}){sufixo}")
        return False
    except requests.RequestException as e:
        logger.error(
            f"Erro ao enviar mensagem no Telegram: {type(e).__name__} "
            "(falha de conexão, sem resposta do servidor)"
        )
        return False


def _linha_relevancia(pontos: int) -> str:
    cheias = (pontos + 1) // 2
    return "⭐" * cheias + "☆" * (5 - cheias) + f" ({pontos}/10)"


def _teclado_feedback(job_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "👍", "callback_data": f"fb|1|{job_id}"},
            {"text": "👎", "callback_data": f"fb|0|{job_id}"},
        ]]
    }


def _linha_aviso_antiga(job) -> str:
    if not job.publicacao_antiga:
        return ""
    return f"⚠️ <b>Postada {_html(job.publicado_em_legivel)}</b> — pode já estar preenchida.\n"


def notificar_vaga(job) -> bool:
    linha_publicacao = f"<b>Publicada:</b> {_html(job.publicado_em_legivel)}\n" if job.publicado_em else ""
    linha_modalidade = f"<b>Modalidade:</b> {_html(job.modalidade)}\n" if job.modalidade else ""
    texto = (
        f"🚨 <b>Nova vaga encontrada!</b>\n\n"
        f"{_linha_aviso_antiga(job)}"
        f"<b>Relevância:</b> {_linha_relevancia(job.relevancia)}\n"
        f"<b>Motivo:</b> {_html(job.motivo)}\n"
        f"<b>Empresa:</b> {_html(job.empresa)}\n"
        f"<b>Cargo:</b> {_html(job.titulo)}\n"
        f"<b>Nível:</b> {_html(job.senioridade)}\n"
        f"<b>Local:</b> {_html(job.local)}\n"
        f"{linha_modalidade}"
        f"<b>Site:</b> {_html(job.site)}\n"
        f"{linha_publicacao}\n"
        f"Encontrada agora\n\n"
        f"<b>Link:</b>\n{_html(job.link)}"
    )
    return enviar_mensagem(texto, reply_markup=_teclado_feedback(job.id))


def notificar_vaga_exploratoria(job) -> bool:
    linha_modalidade = f"<b>Modalidade:</b> {_html(job.modalidade)}\n" if job.modalidade else ""
    texto = (
        f"🧭 <b>Vaga exploratória (Portugal/Espanha)</b>\n\n"
        f"{_linha_aviso_antiga(job)}"
        f"<b>Relevância:</b> {_linha_relevancia(job.relevancia)}\n"
        f"<b>Motivo:</b> {_html(job.motivo)}\n"
        f"<b>Empresa:</b> {_html(job.empresa)}\n"
        f"<b>Cargo:</b> {_html(job.titulo)}\n"
        f"<b>Nível:</b> {_html(job.senioridade)}\n"
        f"<b>Local:</b> {_html(job.local)}\n"
        f"{linha_modalidade}"
        f"<b>Site:</b> {_html(job.site)}\n\n"
        f"Achada via busca por Portugal/Espanha — modalidade não confirmada "
        f"como remota, pode ser presencial ou híbrida. Confirma no link.\n\n"
        f"<b>Link:</b>\n{_html(job.link)}"
    )
    return enviar_mensagem(texto, reply_markup=_teclado_feedback(job.id))


_LIMITE_CHARS_DIGEST = 3500

_ORDEM_FAMILIAS_DIGEST = {
    "Customer Success Manager": 0,
    "Customer Success Analyst / Specialist": 1,
    "Product Owner": 2,
    "Product Manager": 3,
    "Outros cargos relacionados": 4,
}
_ORDEM_SENIORIDADES_DIGEST = {"Sênior": 0, "Pleno": 1, "Não informada": 2}


def _familia_digest(titulo: str) -> str:
    titulo_normalizado = str(titulo).lower()
    if "product owner" in titulo_normalizado or re.search(r"\bpo\b", titulo_normalizado):
        return "Product Owner"
    if "product manager" in titulo_normalizado or "gerente de produto" in titulo_normalizado:
        return "Product Manager"
    if (
        "customer success manager" in titulo_normalizado
        or "gerente de customer success" in titulo_normalizado
        or "gerente de éxito del cliente" in titulo_normalizado
    ):
        return "Customer Success Manager"
    if "customer success" in titulo_normalizado or "éxito del cliente" in titulo_normalizado:
        return "Customer Success Analyst / Specialist"
    return "Outros cargos relacionados"


def _senioridade_digest(titulo: str) -> str:
    titulo_normalizado = str(titulo).lower()
    if re.search(r"\b(sênior|senior|sr)\b", titulo_normalizado):
        return "Sênior"
    if re.search(r"\b(pleno|pl)\b", titulo_normalizado):
        return "Pleno"
    return "Não informada"


def montar_digest(vagas: list[tuple], rotulo_perfil: str) -> list[str]:
    grupos: dict[tuple[str, str], list[str]] = {}
    for titulo, empresa, link, relevancia, exploratoria in vagas:
        familia = _familia_digest(titulo)
        senioridade = _senioridade_digest(titulo)
        linha = (
            f'{"🧭" if exploratoria else "•"} {_linha_relevancia(relevancia or 0)} '
            f'<a href="{_html(link)}">{_html(titulo)}</a> — {_html(empresa)}'
        )
        grupos.setdefault((familia, senioridade), []).append(linha)

    linhas = []
    for (familia, senioridade), itens in sorted(
        grupos.items(),
        key=lambda grupo: (
            _ORDEM_FAMILIAS_DIGEST[grupo[0][0]],
            _ORDEM_SENIORIDADES_DIGEST[grupo[0][1]],
        ),
    ):
        linhas.append(
            f"<b>{_html(familia)}</b> · <i>{_html(senioridade)}</i> "
            f"({len(itens)} vaga(s))"
        )
        linhas.extend(itens)

    partes: list[list[str]] = []
    parte_atual: list[str] = []
    tamanho_atual = 0
    for linha in linhas:
        if parte_atual and tamanho_atual + len(linha) + 1 > _LIMITE_CHARS_DIGEST:
            partes.append(parte_atual)
            parte_atual, tamanho_atual = [], 0
        parte_atual.append(linha)
        tamanho_atual += len(linha) + 1
    if parte_atual:
        partes.append(parte_atual)

    total_partes = len(partes)
    mensagens = []
    for i, parte in enumerate(partes, start=1):
        cabecalho = f"📋 <b>Digest diário — {_html(rotulo_perfil)}</b> ({len(vagas)} vaga(s))"
        if total_partes > 1:
            cabecalho += f" — parte {i}/{total_partes}"
        mensagens.append(cabecalho + "\n\n" + "\n".join(parte))
    return mensagens


def enviar_digest(vagas: list[tuple], rotulo_perfil: str) -> bool:
    if not vagas:
        return True
    return all(enviar_mensagem(mensagem) for mensagem in montar_digest(vagas, rotulo_perfil))


def _chamar_api_telegram(metodo: str, payload: dict) -> dict | None:
    if not TELEGRAM_BOT_TOKEN:
        return None

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{metodo}"
    try:
        resposta = requests.post(url, data=payload, timeout=10)
        resposta.raise_for_status()
        dados = resposta.json()
        if not dados.get("ok", False):
            logger.error(f"Erro ao chamar Telegram {metodo}: {dados.get('description', 'sem descrição')}")
            return None
        return dados
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        motivo = e.response.reason if e.response is not None else "sem detalhe"
        logger.error(f"Erro ao chamar Telegram {metodo}: HTTP {status} ({motivo})")
        return None
    except requests.RequestException as e:
        logger.error(
            f"Erro ao chamar Telegram {metodo}: {type(e).__name__} "
            "(falha de conexão, sem resposta do servidor)"
        )
        return None


def _parsear_callback_data(data: str) -> tuple[str, str] | None:
    partes = (data or "").split("|")
    if len(partes) != 3 or partes[0] != "fb" or partes[1] not in ("1", "0"):
        return None
    return partes[2], ("positivo" if partes[1] == "1" else "negativo")


_OFFSET_CHAVE = "telegram_update_offset"


def processar_feedback_pendente():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    payload = {
        "timeout": 0,
        "limit": 100,
        "allowed_updates": json.dumps(["callback_query"]),
    }
    offset_salvo = obter_metadado(_OFFSET_CHAVE)
    if offset_salvo:
        payload["offset"] = str(int(offset_salvo) + 1)

    resultado = _chamar_api_telegram("getUpdates", payload)
    if resultado is None or not resultado.get("ok"):
        return

    updates = resultado.get("result", [])
    if not updates:
        return

    maior_update_id = None
    for update in updates:
        update_id = update.get("update_id")
        if update_id is not None:
            maior_update_id = update_id if maior_update_id is None else max(maior_update_id, update_id)

        callback = update.get("callback_query")
        if not callback:
            continue

        mensagem = callback.get("message") or {}
        chat_id = str((mensagem.get("chat") or {}).get("id", ""))
        if not chat_id or chat_id != str(TELEGRAM_CHAT_ID):
            continue

        parseado = _parsear_callback_data(callback.get("data", ""))
        if parseado is not None:
            job_id, feedback = parseado
            definir_feedback(job_id, feedback)
            emoji = "👍" if feedback == "positivo" else "👎"
            texto_toast = f"Registrado: {emoji}"
            novo_teclado = {"inline_keyboard": [[{"text": f"✅ Registrado: {emoji}", "callback_data": "fb|ok|-"}]]}
        else:
            texto_toast = "Já registrado."
            novo_teclado = None

        _chamar_api_telegram("answerCallbackQuery", {
            "callback_query_id": callback["id"],
            "text": texto_toast,
        })

        if novo_teclado is not None and mensagem.get("message_id"):
            _chamar_api_telegram("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": mensagem["message_id"],
                "reply_markup": json.dumps(novo_teclado),
            })

    if maior_update_id is not None:
        definir_metadado(_OFFSET_CHAVE, str(maior_update_id))
