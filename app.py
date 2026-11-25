import asyncio
import base64
import json
import os
import random
import re
import time
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from PIL import Image, ImageDraw, ImageFilter, ImageStat

TZ_BR = ZoneInfo("America/Sao_Paulo")
BASE_DIR = Path(__file__).resolve().parent
MONITOR_DIR = BASE_DIR / "IMAGENS_MONITOR"
INTERVALO_SEG = 120
MODO_DEMO_RETORNO_BRAVO = True

app = FastAPI(title="GUARDIAO DF-360 — SSPDF")

_MODELOS_CACHE = {
    "lista": [],
    "expira": 0.0,
    "chave": ""
}

TERMOS_BLOQUEADOS_VISAO = (
    "tts",
    "audio",
    "live",
    "embedding",
    "imagen",
    "image",
    "veo",
    "lyria",
    "robotics",
    "computer-use",
    "aqa",
    "latest",
    "-pro",
    "preview"
)


def obter_chave_api() -> str:
    for nome in ("GUARDIAO_CHAVE_VISAO", "GUARDIAO_API_KEY", "GEMINI_API_KEY"):
        val = os.environ.get(nome, "").strip().strip('"').strip("'")
        if val:
            return val
    return ""


def obter_modelo_env() -> str:
    for nome in ("GUARDIAO_MODELO_VISAO", "GUARDIAO_MODEL", "GEMINI_MODEL"):
        val = os.environ.get(nome, "").strip().strip('"').strip("'")
        if val and not _modelo_bloqueado(val):
            return val
    return ""


def _modelo_bloqueado(nome: str) -> bool:
    n = (nome or "").lower()
    return any(t in n for t in TERMOS_BLOQUEADOS_VISAO)


def agora_br() -> datetime:
    return datetime.now(TZ_BR)


def listar_imagens():
    extensoes = {".jpg", ".jpeg", ".png", ".webp"}
    encontradas = []
    vistos = set()

    for pasta in (BASE_DIR, MONITOR_DIR):
        if pasta.exists() and pasta.is_dir():
            for arq in sorted(pasta.iterdir()):
                if arq.is_file() and arq.suffix.lower() in extensoes:
                    chave = arq.name.lower()
                    if chave not in vistos:
                        vistos.add(chave)
                        encontradas.append(arq)
    return encontradas


STATE = {
    "indice": -1,
    "atual": None,
    "pausado": False,
    "ultima_troca": 0.0,
    "historico": [],
    "mensagens": [
        {
            "origem": "GUARDIAO",
            "hora": agora_br().strftime("%H:%M:%S"),
            "texto": "GUARDIAO DF-360 operacional. Monitoramento visual autonomo ativo."
        }
    ],
    "retorno_bravo": [
        {
            "hora": agora_br().strftime("%H:%M:%S"),
            "ocorrencia_id": "SISTEMA",
            "texto": "Aguardando despacho do ALFA para a equipe BRAVO."
        }
    ]
}


def inspecionar_imagem(caminho: Path):
    img_w, img_h = 1, 1
    brilho = 128.0
    contraste = 50.0
    densidade_bordas = 10.0
    razao_regiao = 1.0
    obs_cena = []
    b64_data = ""
    mime = "image/jpeg"

    try:
        with Image.open(caminho) as im:
            im_rgb = im.convert("RGB")
            img_w, img_h = im_rgb.size
            st = ImageStat.Stat(im_rgb)
            brilho = sum(st.mean) / 3.0
            contraste = sum(st.stddev) / 3.0

            if brilho < 55:
                obs_cena.append("Baixa luminosidade")
            elif brilho > 215:
                obs_cena.append("Alta luminosidade")
            if contraste < 25:
                obs_cena.append("Baixo contraste")
            if min(img_w, img_h) < 180 or (img_w * img_h) < 60000:
                obs_cena.append("Resolucao reduzida")

            pequena = im_rgb.copy()
            pequena.thumbnail((320, 320))
            pw, ph = pequena.size
            cinza = pequena.convert("L")
            bordas = cinza.filter(ImageFilter.FIND_EDGES)
            densidade_bordas = ImageStat.Stat(bordas).mean[0]
            media_c = ImageStat.Stat(cinza).mean[0]
            px_c = cinza.load()
            px_b = bordas.load()

            min_x, min_y = pw, ph
            max_x, max_y = -1, -1
            for y in range(ph):
                for x in range(pw):
                    if abs(px_c[x, y] - media_c) > 38 or px_b[x, y] > 45:
                        if x < min_x:
                            min_x = x
                        if x > max_x:
                            max_x = x
                        if y < min_y:
                            min_y = y
                        if y > max_y:
                            max_y = y
            if max_x > min_x and max_y > min_y:
                razao_regiao = max(1, max_x - min_x + 1) / float(max(1, max_y - min_y + 1))

            otimizada = im_rgb.copy()
            otimizada.thumbnail((960, 960))
            buf = BytesIO()
            otimizada.save(buf, format="JPEG", quality=82, optimize=True)
            b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as exc:
        obs_cena.append(f"Leitura visual com restricao: {exc}")
        try:
            b64_data = base64.b64encode(caminho.read_bytes()).decode("utf-8")
        except Exception:
            b64_data = ""

    if not obs_cena:
        obs_cena.append("Enquadramento e iluminacao regulares")

    detalhes = (
        f"{img_w}x{img_h}px | Brilho {round(brilho, 1)} | "
        f"Contraste {round(contraste, 1)} | " + " | ".join(obs_cena)
    )
    return {
        "mime": mime,
        "b64": b64_data,
        "detalhes": detalhes,
        "brilho": brilho,
        "contraste": contraste,
        "bordas": densidade_bordas,
        "razao": razao_regiao,
        "obs": obs_cena
    }


def desenhar_retangulo_arma(caminho: Path, caixa, rotulo: str = "ARMA") -> bytes:
    with Image.open(caminho) as im:
        im_rgb = im.convert("RGB")
        w, h = im_rgb.size
        draw = ImageDraw.Draw(im_rgb)

        x1 = y1 = x2 = y2 = None
        if isinstance(caixa, (list, tuple)) and len(caixa) == 4:
            vals = [float(v) for v in caixa]
            if max(vals) <= 1.0:
                ymin, xmin, ymax, xmax = vals[0] * h, vals[1] * w, vals[2] * h, vals[3] * w
            elif max(vals) <= 1000.0:
                ymin, xmin, ymax, xmax = (vals[0] / 1000.0) * h, (vals[1] / 1000.0) * w, (vals[2] / 1000.0) * h, (vals[3] / 1000.0) * w
            else:
                xmin, ymin, xmax, ymax = vals
            x1 = max(0, min(w - 1, int(round(min(xmin, xmax)))))
            y1 = max(0, min(h - 1, int(round(min(ymin, ymax)))))
            x2 = max(0, min(w - 1, int(round(max(xmin, xmax)))))
            y2 = max(0, min(h - 1, int(round(max(ymin, ymax)))))

        if x1 is not None and x2 > x1 and y2 > y1:
            espessura = max(3, int(round(min(w, h) * 0.008)))
            for i in range(espessura):
                draw.rectangle([x1 - i, y1 - i, x2 + i, y2 + i], outline=(255, 30, 30))
            texto = (rotulo or "ARMA").upper()[:28]
            box_topo = max(0, y1 - 22)
            draw.rectangle([x1, box_topo, min(w - 1, x1 + max(70, len(texto) * 8)), box_topo + 20], fill=(220, 38, 38))
            draw.text((x1 + 5, box_topo + 4), texto, fill=(255, 255, 255))

        buf = BytesIO()
        im_rgb.save(buf, format="JPEG", quality=88)
        return buf.getvalue()


def obter_modelos_disponiveis(chave: str):
    agora = time.time()
    if (
        _MODELOS_CACHE["lista"]
        and _MODELOS_CACHE["chave"] == chave
        and agora < _MODELOS_CACHE["expira"]
    ):
        return _MODELOS_CACHE["lista"]

    modelo_env = obter_modelo_env()
    padrao = [
        m for m in [
            modelo_env,
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash"
        ] if m and not _modelo_bloqueado(m)
    ]

    descobertos = []
    if chave:
        for ver in ("v1beta", "v1"):
            try:
                r = requests.get(
                    f"https://generativelanguage.googleapis.com/{ver}/models?key={chave}",
                    timeout=8
                )
                if r.status_code == 200:
                    for item in (r.json().get("models") or []):
                        nome = str(item.get("name") or "").replace("models/", "").strip()
                        metodos = item.get("supportedGenerationMethods") or []
                        if nome and ("generateContent" in metodos or not metodos):
                            if "gemini" in nome.lower() and not _modelo_bloqueado(nome) and nome not in descobertos:
                                descobertos.append(nome)
                    if descobertos:
                        break
            except Exception:
                pass

    ordenados = []
    for m in padrao + descobertos:
        if m and not _modelo_bloqueado(m) and m not in ordenados:
            ordenados.append(m)

    _MODELOS_CACHE["lista"] = ordenados[:6]
    _MODELOS_CACHE["expira"] = agora + 900.0
    _MODELOS_CACHE["chave"] = chave
    return _MODELOS_CACHE["lista"]


def analise_visual_fallback(info: dict, motivo: str = ""):
    objetos = []
    pontos = list(info.get("obs") or [])
    razao = info.get("razao", 1.0)
    bordas = info.get("bordas", 0.0)
    contraste = info.get("contraste", 0.0)

    if razao >= 2.2 and bordas >= 15.0 and contraste >= 25.0:
        conf = round(min(92.0, max(65.0, 60.0 + bordas * 0.4 + contraste * 0.15)), 1)
        objetos.append({
            "rotulo": "Estrutura alongada na cena",
            "confianca": f"{conf}%",
            "detalhe": f"Proporcao horizontal {razao:.1f}:1 com contorno definido"
        })
        nivel = "OBSERVACAO"
        status = "OBSERVACAO_VISUAL"
        sintese = f"Estrutura alongada identificada na imagem ({razao:.1f}:1); verificacao visual recomendada."
        relato = (
            f"A analise direta dos pixels evidencia elemento de formato alongado na cena "
            f"(proporcao aproximada {razao:.1f}:1, densidade de contorno {bordas:.1f}). "
            "Verifique o enquadramento para confirmacao da natureza exata do objeto."
        )
        pontos.append("Conferir elemento alongado em detalhe")
    elif bordas >= 16.0:
        conf = round(min(86.0, max(58.0, 55.0 + bordas * 0.35)), 1)
        objetos.append({
            "rotulo": "Elemento com contorno definido",
            "confianca": f"{conf}%",
            "detalhe": "Regiao de contraste/borda destacada no quadro"
        })
        nivel = "NORMAL"
        status = "REGISTRO_VISUAL"
        sintese = "Cena registrada com elementos de contorno definido; sem alerta critico isolado."
        relato = (
            "A inspecao visual identifica objetos ou superficies com contornos nitidos no quadro, "
            "sem indicio conclusivo de armamento ou ameaca imediata isolada."
        )
    else:
        nivel = "NORMAL"
        status = "SEM_ANOMALIA_APARENTE"
        sintese = "Cena sem anomalias visuais evidentes no enquadramento atual."
        relato = (
            "A leitura visual da imagem indica cena sem elementos criticos isolados de forma conclusiva "
            "no momento da captura."
        )

    if motivo:
        pontos.append(f"Contingencia: {motivo}")

    return {
        "modo_analise": "CONTINGENCIA_PIXELS",
        "status": status,
        "nivel": nivel,
        "pessoas_visiveis": 0,
        "objetos": objetos,
        "sintese_operacional": sintese,
        "relato_visual": relato,
        "pontos_atencao": pontos or ["Monitoramento continuo em curso"],
        "detalhes_cena": info["detalhes"],
        "caixa_arma": None,
        "rotulo_arma": ""
    }


def extrair_json_robusto(texto: str):
    if not texto:
        return None
    limpo = re.sub(r"```(?:json)?|```", "", texto, flags=re.IGNORECASE).strip()
    try:
        obj = json.loads(limpo)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    ini = limpo.find("{")
    fim = limpo.rfind("}")
    if ini != -1 and fim != -1 and fim > ini:
        try:
            obj = json.loads(limpo[ini:fim + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _chamar_visao_sync(mime: str, img_b64: str):
    chave = obter_chave_api()
    if not chave:
        print("[GUARDIAO] Chave de visao nao encontrada no ambiente.", flush=True)
        return None, "Chave de analise nao configurada"
    if not img_b64:
        print("[GUARDIAO] Imagem vazia/sem dados para envio.", flush=True)
        return None, "Imagem sem dados visuais"

    prompt = (
        "Voce e o modulo de analise visual operacional do GUARDIAO DF-360 para apoio decisorio do comando (ALFA / SSPDF). "
        "Descreva com precisao extrema e fidelidade absoluta o que esta visivel na imagem (ignore completamente o nome do arquivo).\n"
        "Detalhe sempre:\n"
        "1) Pessoas visiveis: quantidade, vestimentas/farda (ex.: farda policial/militar, traje civil, roupa escura), postura/posicao (em pe, sentada, deitada no chao, aparentemente ferida ou imobilizada, apontando para cima/frente, mao proxima ao coldre, etc.) e proximidade/relacao com outras pessoas na cena.\n"
        "2) Armas e objetos: especifique se ha arma de fogo longa ou curta (fuzil, carabina, espingarda, pistola, revolver) ou arma branca, onde esta (sobre o corpo/chao, dentro do coldre na cintura, empunhada, apontada para cima/alguem, cao/gatilho aparente) e outros objetos na mao ou na cena.\n"
        "3) Se houver arma de fogo ou arma branca visivel na cena, informe em 'caixa_arma' as coordenadas normalizadas de 0 a 1000 no formato [ymin, xmin, ymax, xmax] delimitando a arma.\n"
        "Responda APENAS com um JSON valido (sem markdown, sem texto fora do JSON):\n"
        "{\n"
        '  "arma_fogo": bool,\n'
        '  "arma_branca": bool,\n'
        '  "atitude_ameaca": bool,\n'
        '  "objeto_na_mao": "string ou vazio",\n'
        '  "pessoas_visiveis": int,\n'
        '  "nivel_sugerido": "NORMAL" | "OBSERVACAO" | "ATENCAO_ELEVADA" | "ALTO_RISCO",\n'
        '  "objetos": [{"rotulo": "string", "confianca": 0-100, "detalhe": "string"}],\n'
        '  "sintese_operacional": "string direta com os fatos criticos, arma/coldre/mira, pessoas (inclusive se deitada ou aparentemente ferida) e atitude para decisao do ALFA",\n'
        '  "relato_detalhado": "string completa e fiel descrevendo pessoas, vestimenta/farda, postura (em pe, sentada, deitada, aparentemente ferida), arma (coldre, mao, sobre o corpo, direcao da mira), proximidade de terceiros e ambiente",\n'
        '  "pontos_atencao": ["string"],\n'
        '  "caixa_arma": [ymin, xmin, ymax, xmax] ou null\n'
        "}\n"
        "Seja claro, objetivo e fiel ao que se ve. Nao mencione tecnologia, modelos, APIs ou plataformas."
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": img_b64}}
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json"
        }
    }

    ultimo_erro = "Sem resposta da visao externa"
    modelos = obter_modelos_disponiveis(chave)
    for modelo in modelos:
        for ver in ("v1beta", "v1"):
            url = f"https://generativelanguage.googleapis.com/{ver}/models/{modelo}:generateContent?key={chave}"
            for tentativa in range(3):
                try:
                    resp = requests.post(url, json=payload, timeout=20)
                    if resp.status_code == 404:
                        ultimo_erro = f"Modelo {modelo} indisponivel ({ver})"
                        break
                    if resp.status_code in (400, 401, 403):
                        msg = f"Falha de autorizacao/chave (HTTP {resp.status_code})"
                        print(f"[GUARDIAO] {msg} em {modelo} ({ver}): {resp.text[:160]}", flush=True)
                        return None, msg
                    if resp.status_code == 429:
                        ultimo_erro = "Cota/requisicoes limitadas no momento (HTTP 429)"
                        print(f"[GUARDIAO] {ultimo_erro} em {modelo} ({ver}) tentativa {tentativa + 1}/3", flush=True)
                        time.sleep(2.5 * (tentativa + 1))
                        continue
                    if resp.status_code in (500, 502, 503, 504):
                        ultimo_erro = f"Instabilidade temporaria (HTTP {resp.status_code})"
                        print(f"[GUARDIAO] {ultimo_erro} em {modelo} ({ver}) tentativa {tentativa + 1}/3", flush=True)
                        time.sleep(1.5 * (tentativa + 1))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    candidatos = data.get("candidates") or []
                    if not candidatos:
                        ultimo_erro = "Resposta sem candidatos"
                        break
                    partes = (candidatos[0].get("content") or {}).get("parts") or []
                    texto_resp = "".join(str(p.get("text", "")) for p in partes if isinstance(p, dict)).strip()
                    parsed = extrair_json_robusto(texto_resp)
                    if parsed is not None:
                        print(f"[GUARDIAO] Analise concluida via {modelo} ({ver}).", flush=True)
                        return parsed, ""
                    if texto_resp:
                        print(f"[GUARDIAO] Resposta em texto livre via {modelo} ({ver}).", flush=True)
                        return {
                            "arma_fogo": False,
                            "arma_branca": False,
                            "atitude_ameaca": False,
                            "objeto_na_mao": "",
                            "pessoas_visiveis": 0,
                            "nivel_sugerido": "OBSERVACAO",
                            "objetos": [],
                            "sintese_operacional": texto_resp[:220],
                            "relato_detalhado": texto_resp,
                            "pontos_atencao": [],
                            "caixa_arma": None
                        }, ""
                    break
                except Exception as exc:
                    ultimo_erro = f"Erro de conexao: {type(exc).__name__}"
                    print(f"[GUARDIAO] Falha em {modelo} ({ver}) tentativa {tentativa + 1}/3: {exc}", flush=True)
                    time.sleep(1.0)

    print(f"[GUARDIAO] Fallback acionado: {ultimo_erro}", flush=True)
    return None, ultimo_erro


async def consultar_visao_externa(mime: str, img_b64: str):
    return await asyncio.to_thread(_chamar_visao_sync, mime, img_b64)


def sanitizar_texto_operacional(txt: str) -> str:
    if not txt:
        return ""
    limpo = re.sub(
        r"(?i)\b(gemini|google\s*ai\s*studio|generativelanguage|render|gemini_api_key|guardiao_chave_visao|sem_chave_ia_configurada)\b",
        "sensor visual",
        txt
    )
    return re.sub(r"\s{2,}", " ", limpo).strip()


async def analisar_imagem_ia(caminho: Path):
    info = await asyncio.to_thread(inspecionar_imagem, caminho)
    parsed, motivo_erro = await consultar_visao_externa(info["mime"], info["b64"])

    if not parsed:
        return analise_visual_fallback(info, motivo_erro)

    arma_fogo = bool(parsed.get("arma_fogo", False))
    arma_branca = bool(parsed.get("arma_branca", False))
    atitude_ameaca = bool(parsed.get("atitude_ameaca", False))
    objeto_mao = sanitizar_texto_operacional(str(parsed.get("objeto_na_mao") or "").strip())

    try:
        pessoas = max(0, int(parsed.get("pessoas_visiveis", 0) or 0))
    except Exception:
        pessoas = 0

    objetos = []
    if objeto_mao:
        objetos.append({
            "rotulo": objeto_mao,
            "confianca": "",
            "detalhe": "Objeto empunhado/segurado/na cena"
        })

    if isinstance(parsed.get("objetos"), list):
        for item in parsed.get("objetos"):
            if isinstance(item, dict):
                rotulo = sanitizar_texto_operacional(str(item.get("rotulo") or item.get("classe") or "Elemento"))
                det = sanitizar_texto_operacional(str(item.get("detalhe") or ""))
                conf_raw = item.get("confianca")
                conf_txt = ""
                try:
                    if conf_raw is not None and str(conf_raw).strip() != "":
                        c_val = float(conf_raw)
                        if 0.0 <= c_val <= 1.0:
                            c_val *= 100.0
                        conf_txt = f"{round(c_val, 1)}%"
                except Exception:
                    conf_txt = ""
                if rotulo and not any(o["rotulo"].lower() == rotulo.lower() for o in objetos):
                    objetos.append({"rotulo": rotulo, "confianca": conf_txt, "detalhe": det})

    relato = sanitizar_texto_operacional(
        str(parsed.get("relato_detalhado") or parsed.get("relato") or "Analise visual concluida.")
    )
    sintese = sanitizar_texto_operacional(str(parsed.get("sintese_operacional") or ""))
    if not sintese:
        sintese = relato[:220]

    pontos = []
    if objeto_mao:
        pontos.append(f"Objeto na mao/cena: {objeto_mao}")
    if atitude_ameaca:
        pontos.append("Atitude de ameaca / postura agressiva ou suspeita")
    if isinstance(parsed.get("pontos_atencao"), list):
        for p in parsed.get("pontos_atencao"):
            sp = sanitizar_texto_operacional(str(p))
            if sp and sp not in pontos:
                pontos.append(sp)
    if not pontos:
        pontos = list(info.get("obs") or ["Cena avaliada"])

    nivel_ia = str(parsed.get("nivel_sugerido") or "").strip().upper()
    if arma_fogo or (atitude_ameaca and (arma_branca or bool(objeto_mao))):
        status = "ARMA_DE_FOGO_IDENTIFICADA" if arma_fogo else "AMEACA_COM_OBJETO_IDENTIFICADA"
        nivel = "ALTO_RISCO" if nivel_ia not in ("ATENCAO_ELEVADA", "ALTO_RISCO") else nivel_ia
    elif arma_branca:
        status = "ARMA_BRANCA_IDENTIFICADA"
        nivel = "ATENCAO_ELEVADA" if nivel_ia not in ("OBSERVACAO", "ATENCAO_ELEVADA", "ALTO_RISCO") else nivel_ia
    elif atitude_ameaca:
        status = "ATITUDE_DE_AMEACA_IDENTIFICADA"
        nivel = "ATENCAO_ELEVADA" if nivel_ia not in ("OBSERVACAO", "ATENCAO_ELEVADA", "ALTO_RISCO") else nivel_ia
    else:
        status = "ANALISE_VISUAL_CONCLUIDA"
        nivel = nivel_ia if nivel_ia in ("NORMAL", "OBSERVACAO", "ATENCAO_ELEVADA", "ALTO_RISCO") else "NORMAL"

    caixa_arma = parsed.get("caixa_arma")
    rotulo_arma = ""
    if arma_fogo or arma_branca or caixa_arma:
        rotulo_arma = objeto_mao or (objetos[0]["rotulo"] if objetos else ("ARMA DE FOGO" if arma_fogo else "ARMA BRANCA"))

    return {
        "modo_analise": "SENSOR_VISUAL",
        "status": status,
        "nivel": nivel,
        "pessoas_visiveis": pessoas,
        "objetos": objetos,
        "sintese_operacional": sintese,
        "relato_visual": relato,
        "pontos_atencao": pontos,
        "detalhes_cena": info["detalhes"],
        "caixa_arma": caixa_arma,
        "rotulo_arma": rotulo_arma
    }


async def avancar_ciclo():
    imagens = listar_imagens()
    if not imagens:
        return
    STATE["indice"] = (STATE["indice"] + 1) % len(imagens)
    arq = imagens[STATE["indice"]]
    agora = agora_br()
    analise = await analisar_imagem_ia(arq)

    oc = {
        "id": f"OC-{agora.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4].upper()}",
        "iso_dt": agora.isoformat(),
        "data_hora": agora.strftime("%d/%m/%Y %H:%M:%S"),
        "indice": STATE["indice"] + 1,
        "total": len(imagens),
        "arquivo": arq.name,
        "caminho": str(arq),
        "situacao_fluxo": "ATIVA",
        "retorno_bravo": "",
        **analise
    }

    STATE["atual"] = oc
    STATE["ultima_troca"] = time.time()
    STATE["historico"].append(oc)
    STATE["historico"] = STATE["historico"][-300:]
    STATE["mensagens"].append({
        "origem": "GUARDIAO",
        "hora": agora.strftime("%H:%M:%S"),
        "texto": f"[{oc['id']}] {oc['nivel']} — {oc['sintese_operacional']}"
    })
    STATE["mensagens"] = STATE["mensagens"][-150:]


async def simular_retorno_bravo_demo(oc_id: str):
    if not MODO_DEMO_RETORNO_BRAVO:
        return
    await asyncio.sleep(8)
    hora = agora_br().strftime("%H:%M:%S")
    relatos = [
        "[DEMO] Equipe BRAVO em deslocamento ate o ponto informado pelo ALFA.",
        "[DEMO] Equipe BRAVO no local; averiguacao em andamento.",
        "[DEMO] Equipe BRAVO concluiu averiguacao inicial e aguarda diretriz do ALFA."
    ]
    txt = random.choice(relatos)
    STATE["retorno_bravo"].append({"hora": hora, "ocorrencia_id": oc_id, "texto": txt})
    STATE["retorno_bravo"] = STATE["retorno_bravo"][-30:]
    for o in STATE["historico"]:
        if o["id"] == oc_id:
            o["retorno_bravo"] = f"[{hora}] {txt}"
    if STATE["atual"] and STATE["atual"]["id"] == oc_id:
        STATE["atual"]["retorno_bravo"] = f"[{hora}] {txt}"


async def loop_autonomo():
    if STATE["atual"] is None:
        await avancar_ciclo()
    while True:
        await asyncio.sleep(1)
        if not STATE["pausado"] and (time.time() - STATE["ultima_troca"]) >= INTERVALO_SEG:
            await avancar_ciclo()


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(loop_autonomo())


@app.get("/imagem")
async def get_imagem():
    oc = STATE["atual"]
    if oc and Path(oc["caminho"]).exists():
        caminho = Path(oc["caminho"])
        if oc.get("caixa_arma"):
            try:
                img_bytes = await asyncio.to_thread(
                    desenhar_retangulo_arma,
                    caminho,
                    oc.get("caixa_arma"),
                    oc.get("rotulo_arma") or "ARMA"
                )
                return Response(content=img_bytes, media_type="image/jpeg")
            except Exception:
                pass
        media = "image/png" if caminho.suffix.lower() == ".png" else ("image/webp" if caminho.suffix.lower() == ".webp" else "image/jpeg")
        return Response(content=caminho.read_bytes(), media_type=media)
    return JSONResponse({"erro": "Sem imagem"}, status_code=404)


@app.get("/api/estado")
async def api_estado():
    restante = max(0, int(INTERVALO_SEG - (time.time() - STATE["ultima_troca"]))) if not STATE["pausado"] else int(INTERVALO_SEG)
    arquivadas = [o for o in STATE["historico"] if o.get("situacao_fluxo") == "ARQUIVADA"]
    agora = agora_br()
    return {
        "agora": agora.strftime("%d/%m/%Y — %H:%M:%S"),
        "restante": restante,
        "pausado": STATE["pausado"],
        "modo_demo_retorno": MODO_DEMO_RETORNO_BRAVO,
        "atual": STATE["atual"],
        "arquivadas": arquivadas[-40:],
        "mensagens": STATE["mensagens"][-40:],
        "retorno_bravo": STATE["retorno_bravo"][-30:]
    }


@app.post("/api/enviar-bravo")
async def enviar_bravo(req: Request):
    dados = await req.json()
    oc_id = dados.get("id") or (STATE["atual"]["id"] if STATE["atual"] else "N/A")
    hora = agora_br().strftime("%H:%M:%S")
    for o in STATE["historico"]:
        if o["id"] == oc_id:
            o["situacao_fluxo"] = "ENVIADO_AO_BRAVO"
    if STATE["atual"] and STATE["atual"]["id"] == oc_id:
        STATE["atual"]["situacao_fluxo"] = "ENVIADO_AO_BRAVO"
    STATE["mensagens"].append({"origem": "ALFA -> BRAVO", "hora": hora, "texto": f"Ocorrencia {oc_id} enviada a equipe BRAVO."})
    STATE["mensagens"] = STATE["mensagens"][-150:]
    if MODO_DEMO_RETORNO_BRAVO:
        asyncio.create_task(simular_retorno_bravo_demo(oc_id))
    return {"ok": True}


@app.post("/api/retorno-bravo")
async def retorno_bravo(req: Request):
    dados = await req.json()
    texto = (dados.get("texto") or "").strip()
    oc_id = (dados.get("oc_id") or (STATE["atual"]["id"] if STATE["atual"] else "GERAL")).strip()
    if texto:
        hora = agora_br().strftime("%H:%M:%S")
        STATE["retorno_bravo"].append({"hora": hora, "ocorrencia_id": oc_id, "texto": texto})
        STATE["retorno_bravo"] = STATE["retorno_bravo"][-30:]
        for o in STATE["historico"]:
            if o["id"] == oc_id:
                o["retorno_bravo"] = f"[{hora}] {texto}"
        if STATE["atual"] and STATE["atual"]["id"] == oc_id:
            STATE["atual"]["retorno_bravo"] = f"[{hora}] {texto}"
    return {"ok": True}


@app.post("/api/arquivar")
async def arquivar(req: Request):
    dados = await req.json()
    oc_id = dados.get("id") or (STATE["atual"]["id"] if STATE["atual"] else "")
    for o in STATE["historico"]:
        if o["id"] == oc_id:
            o["situacao_fluxo"] = "ARQUIVADA"
    if STATE["atual"] and STATE["atual"]["id"] == oc_id:
        await avancar_ciclo()
    return {"ok": True}


@app.post("/api/desarquivar-periodo")
async def desarquivar_periodo(req: Request):
    dados = await req.json()
    inicio_str = (dados.get("inicio") or "").strip()
    fim_str = (dados.get("fim") or "").strip()
    dt_ini = datetime.fromisoformat(inicio_str) if inicio_str else None
    dt_fim = datetime.fromisoformat(fim_str) if fim_str else None
    qtd = 0
    for o in STATE["historico"]:
        if o.get("situacao_fluxo") == "ARQUIVADA":
            try:
                odt = datetime.fromisoformat(o.get("iso_dt", "")).replace(tzinfo=None)
            except Exception:
                odt = None
            dentro = True
            if dt_ini and odt and odt < dt_ini:
                dentro = False
            if dt_fim and odt and odt > dt_fim:
                dentro = False
            if dentro:
                o["situacao_fluxo"] = "ATIVA"
                qtd += 1
    ativas_restantes = [o for o in STATE["historico"] if o.get("situacao_fluxo") != "ARQUIVADA"]
    if ativas_restantes and (STATE["atual"] is None or STATE["atual"].get("situacao_fluxo") == "ARQUIVADA"):
        STATE["atual"] = ativas_restantes[-1]
    return {"ok": True, "desarquivadas": qtd}


@app.post("/api/mensagem")
async def mensagem(req: Request):
    dados = await req.json()
    texto = (dados.get("texto") or "").strip()
    if texto:
        hora = agora_br().strftime("%H:%M:%S")
        STATE["mensagens"].append({"origem": "ALFA", "hora": hora, "texto": texto})
        STATE["mensagens"].append({"origem": "GUARDIAO", "hora": hora, "texto": f"Registrado pelo GUARDIAO: '{texto}'."})
        STATE["mensagens"] = STATE["mensagens"][-150:]
    return {"ok": True}


@app.post("/api/pausa")
async def pausa():
    STATE["pausado"] = not STATE["pausado"]
    if not STATE["pausado"]:
        STATE["ultima_troca"] = time.time()
    return {"ok": True, "pausado": STATE["pausado"]}


@app.get("/", response_class=HTMLResponse)
async def home():
    return """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GUARDIAO DF-360</title>
<style>
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;width:100%;max-width:100%;overflow-x:clip;background:#090d16;color:#e6eef8;font-family:Segoe UI,sans-serif}
header{background:#0d2545;padding:10px 14px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;border-bottom:2px solid #2563eb;width:100%}
header>div{min-width:0;max-width:100%;overflow-wrap:anywhere}
.relogio{font-family:monospace;font-size:15px;color:#7dd3fc;font-weight:bold;white-space:nowrap}
.grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:12px;padding:12px;width:100%;max-width:100%}
.grid>div{min-width:0;max-width:100%}
.card{background:#111827;border:1px solid #243247;border-radius:8px;padding:10px;margin-bottom:10px;min-width:0;max-width:100%;overflow:hidden}
.card h3{margin:0 0 8px 0;color:#93c5fd;font-size:13px;overflow-wrap:anywhere}
img.foto{max-width:100%;width:auto;max-height:270px;border-radius:6px;display:block;margin:0 auto;background:#000}
.botoes{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;align-items:center;max-width:100%}
button{padding:7px 10px;border:none;border-radius:6px;cursor:pointer;font-weight:600;color:#fff;font-size:12px;max-width:100%}
.b-bravo{background:#dc2626}.b-arq{background:#2563eb}.b-desarq{background:#059669}.b-pausa{background:#475569}.b-env{background:#0284c7}
input{background:#0b101b;color:#fff;border:1px solid #334155;border-radius:6px;padding:6px;font-size:12px;min-width:0;max-width:100%}
.box{background:#0b101b;border:1px solid #1e293b;border-radius:6px;padding:8px;max-height:220px;overflow-y:auto;overflow-x:hidden;font-size:12px;overflow-wrap:anywhere;word-break:break-word;max-width:100%}
.item{border-bottom:1px solid #1e293b;padding:5px 0}
.linha-rel{margin-top:4px;line-height:1.35}
.crit{color:#fca5a5}.med{color:#fcd34d}.seg{color:#86efac}
@media(max-width:980px){.grid{grid-template-columns:minmax(0,1fr)}}
</style>
</head>
<body>
<header>
  <div><b>GUARDIAO DF-360 — SSPDF / DF-360</b> | Ciclo: <span id="ciclo">02:00</span></div>
  <div class="relogio" id="relogio">--/--/---- --:--:--</div>
</header>
<div class="grid">
  <div>
    <div class="card">
      <h3>CAMPO DE VISAO DO GUARDIAO</h3>
      <img id="foto" class="foto" src="/imagem" alt="Imagem analisada">
      <div id="meta" style="font-size:11px;color:#94a3b8;margin-top:6px;overflow-wrap:anywhere"></div>
      <div class="botoes">
        <button class="b-bravo" onclick="acao('/api/enviar-bravo')">ENVIAR AO BRAVO</button>
        <button class="b-arq" onclick="acao('/api/arquivar')">ARQUIVAR OCORRENCIA</button>
        <button class="b-pausa" onclick="acao('/api/pausa')">PAUSAR / RETOMAR</button>
      </div>
    </div>
    <div class="card">
      <h3>RELATORIO SITUACIONAL DO GUARDIAO (DECISAO ALFA)</h3>
      <div id="relatorio" class="box" style="max-height:260px"></div>
    </div>
    <div class="card">
      <h3>DESARQUIVAR OCORRENCIA POR PERIODO</h3>
      <div class="botoes">
        <input type="datetime-local" id="dtIni">
        <input type="datetime-local" id="dtFim">
        <button class="b-desarq" onclick="desarquivar()">DESARQUIVAR PERIODO</button>
        <span id="msgDesarq" style="font-size:12px;color:#6ee7b7"></span>
      </div>
      <div id="listaArq" class="box" style="margin-top:8px;max-height:115px"></div>
    </div>
  </div>
  <div>
    <div class="card">
      <h3>RETORNO DO BRAVO <span id="badgeDemo" style="font-size:11px;color:#fbbf24"></span></h3>
      <div class="botoes" style="margin-top:0;margin-bottom:8px">
        <input type="text" id="txtBravo" placeholder="Retorno da equipe BRAVO..." style="flex:1 1 140px">
        <button class="b-env" onclick="enviarRetornoBravo()">REGISTRAR RETORNO</button>
      </div>
      <div id="boxBravo" class="box"></div>
    </div>
    <div class="card">
      <h3>COMUNICACAO — ALFA &lt;-&gt; GUARDIAO</h3>
      <div class="botoes" style="margin-top:0;margin-bottom:8px">
        <input type="text" id="txtMsg" placeholder="Mensagem do ALFA para o GUARDIAO..." style="flex:1 1 140px">
        <button class="b-env" onclick="enviarMsg()">ENVIAR</button>
      </div>
      <div id="boxMsg" class="box" style="max-height:360px"></div>
    </div>
  </div>
</div>
<script>
function esc(s){
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
async function acao(url){
  const id = window._atualId || "";
  await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});
  atualizar();
}
async function enviarRetornoBravo(){
  const t = document.getElementById("txtBravo").value.trim();
  if(!t) return;
  await fetch("/api/retorno-bravo",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({texto:t,oc_id:window._atualId||"GERAL"})});
  document.getElementById("txtBravo").value = "";
  atualizar();
}
async function enviarMsg(){
  const t = document.getElementById("txtMsg").value.trim();
  if(!t) return;
  await fetch("/api/mensagem",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({texto:t})});
  document.getElementById("txtMsg").value = "";
  atualizar();
}
async function desarquivar(){
  const inicio = document.getElementById("dtIni").value;
  const fim = document.getElementById("dtFim").value;
  const r = await fetch("/api/desarquivar-periodo",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({inicio,fim})});
  const j = await r.json();
  document.getElementById("msgDesarq").textContent = "Desarquivadas: " + (j.desarquivadas || 0);
  atualizar();
}
function fmtSeg(s){
  const m = String(Math.floor(s/60)).padStart(2,"0");
  const r = String(s%60).padStart(2,"0");
  return m+":"+r;
}
function rolarBaixo(el){
  if(!el) return;
  const pertoDoFim = (el.scrollHeight - el.scrollTop - el.clientHeight) < 40;
  if(pertoDoFim || el.dataset.init !== "1"){
    el.scrollTop = el.scrollHeight;
    el.dataset.init = "1";
  }
}
async function atualizar(){
  const r = await fetch("/api/estado");
  const d = await r.json();
  document.getElementById("relogio").textContent = d.agora;
  document.getElementById("ciclo").textContent = fmtSeg(d.restante) + (d.pausado ? " [PAUSADO]" : "");
  document.getElementById("badgeDemo").textContent = d.modo_demo_retorno ? "(DEMO — RETORNO REAL APOS APROVACAO)" : "(TEMPO REAL BRAVO)";
  const a = d.atual;
  if(a){
    window._atualId = a.id;
    document.getElementById("foto").src = "/imagem?t=" + encodeURIComponent(a.id + "_" + a.situacao_fluxo + "_" + (a.modo_analise || ""));
    document.getElementById("meta").textContent = "ID: "+a.id+" | "+a.arquivo+" ("+a.indice+"/"+a.total+") | "+a.situacao_fluxo+" | "+(a.modo_analise||"");
    const objs = (a.objetos||[]).map(x => {
      const rot = esc(x.rotulo || "");
      const det = x.detalhe ? " [" + esc(x.detalhe) + "]" : "";
      const conf = x.confianca ? " (" + esc(x.confianca) + ")" : "";
      return (rot + det + conf).trim();
    }).filter(Boolean).join("; ") || "Nenhum objeto critico isolado";
    const pontos = (a.pontos_atencao||[]).map(p => esc(p)).join(" | ") || "Sem pontos adicionais";
    const cls = (a.nivel==="ALTO_RISCO"||a.nivel==="ATENCAO_ELEVADA")?"crit":(a.nivel==="OBSERVACAO"||a.nivel==="REGISTRO_OBJETO"?"med":"seg");
    document.getElementById("relatorio").innerHTML =
      "<div class='"+cls+"'><b>NIVEL:</b> "+esc(a.nivel)+" | <b>STATUS:</b> "+esc(a.status)+" | <b>PESSOAS:</b> "+esc(a.pessoas_visiveis ?? 0)+" | <b>MODO:</b> "+esc(a.modo_analise || "SENSOR_VISUAL")+"</div>"+
      "<div class='linha-rel'><b>SINTESE OPERACIONAL (ALFA):</b> "+esc(a.sintese_operacional || a.relato_visual)+"</div>"+
      "<div class='linha-rel'><b>ELEMENTOS / OBJETOS VISIVEIS:</b> "+objs+"</div>"+
      "<div class='linha-rel'><b>RELATO COMPLETO DA CENA:</b> "+esc(a.relato_visual)+"</div>"+
      "<div class='linha-rel'><b>PONTOS DE ATENCAO:</b> "+pontos+"</div>"+
      "<div class='linha-rel'><b>AMBIENTE / TELEMETRIA:</b> "+esc(a.detalhes_cena)+"</div>"+
      (a.retorno_bravo ? "<div class='linha-rel'><b>RETORNO BRAVO:</b> "+esc(a.retorno_bravo)+"</div>" : "");
  }
  const boxBravo = document.getElementById("boxBravo");
  const boxMsg = document.getElementById("boxMsg");
  const listaArq = document.getElementById("listaArq");
  boxBravo.innerHTML = (d.retorno_bravo||[]).map(x => "<div class='item'>["+esc(x.hora)+"] <b>"+esc(x.ocorrencia_id)+":</b> "+esc(x.texto)+"</div>").join("");
  boxMsg.innerHTML = (d.mensagens||[]).map(x => "<div class='item'>["+esc(x.hora)+"] <b>"+esc(x.origem)+":</b> "+esc(x.texto)+"</div>").join("");
  listaArq.innerHTML = (d.arquivadas||[]).map(x => "<div class='item'>[ARQUIVADA] <b>"+esc(x.id)+"</b> — "+esc(x.data_hora)+" — "+esc(x.arquivo)+"</div>").join("");
  rolarBaixo(boxBravo);
  rolarBaixo(boxMsg);
  rolarBaixo(listaArq);
}
atualizar();
setInterval(atualizar, 1000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    porta = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=porta, workers=1)
