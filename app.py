import asyncio
import base64
import json
import os
import random
import re
import sqlite3
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
DB_PATH = BASE_DIR / "guardiao_dados.db"
INTERVALO_SEG = 120
MODO_DEMO_RETORNO_BRAVO = True

app = FastAPI(title="MONITORAMENTO OPERACIONAL")

_MODELOS_CACHE = {
    "lista": [],
    "expira": 0.0,
    "chave": ""
}

_MODELOS_INEXISTENTES_404 = set()

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
    "thinking"
)

TERMOS_REDUCAO_50 = (
    "carteira",
    "couro",
    "celular",
    "smartphone",
    "iphone",
    "telefone"
)

ENDERECOS_DF_OPERACIONAIS = (
    "Eixo Monumental — Trecho 1 (Proximo ao Complexo Cultural), Brasilia/DF",
    "W3 Sul — CRS 508, Asa Sul, Brasilia/DF",
    "Setor Comercial Sul — Quadra 06, Brasilia/DF",
    "L2 Norte — SGAN 607, Asa Norte, Brasilia/DF",
    "EPNB (DF-075) — KM 04, Candangolandia/DF",
    "Avenida Central — QNM 17, Ceilandia/DF",
    "Avenida das Araucarias — Lote 1420, Aguas Claras/DF",
    "Rodoviaria do Plano Piloto — Plataforma Superior, Brasilia/DF",
    "Setor de Diversoes Sul (Conic) — Bloco B, Brasilia/DF",
    "Avenida Elmo Serejo — QNM 02, Taguatinga/DF",
    "EPCT (DF-001) — KM 12, Sobradinho/DF",
    "Setor Central — Quadra 03, Gama/DF"
)


def iniciar_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ocorrencias (
                id TEXT PRIMARY KEY,
                iso_dt TEXT,
                dados_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origem TEXT,
                hora TEXT,
                texto TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS retorno_bravo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hora TEXT,
                ocorrencia_id TEXT,
                texto TEXT
            )
        """)
        conn.commit()


def salvar_ocorrencia_db(oc: dict):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ocorrencias (id, iso_dt, dados_json) VALUES (?, ?, ?)",
                (oc["id"], oc.get("iso_dt", ""), json.dumps(oc, ensure_ascii=False))
            )
            conn.commit()
    except Exception as exc:
        print(f"[GUARDIAO] Erro ao salvar ocorrencia no SQLite: {exc}", flush=True)


def salvar_mensagem_db(origem: str, hora: str, texto: str):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO mensagens (origem, hora, texto) VALUES (?, ?, ?)",
                (origem, hora, texto)
            )
            conn.commit()
    except Exception as exc:
        print(f"[GUARDIAO] Erro ao salvar mensagem no SQLite: {exc}", flush=True)


def salvar_retorno_db(hora: str, ocorrencia_id: str, texto: str):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO retorno_bravo (hora, ocorrencia_id, texto) VALUES (?, ?, ?)",
                (hora, ocorrencia_id, texto)
            )
            conn.commit()
    except Exception as exc:
        print(f"[GUARDIAO] Erro ao salvar retorno no SQLite: {exc}", flush=True)


def carregar_estado_db():
    iniciar_db()
    historico = []
    mensagens = []
    retornos = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows_oc = conn.execute("SELECT dados_json FROM ocorrencias ORDER BY iso_dt ASC").fetchall()
            for (dj,) in rows_oc:
                try:
                    historico.append(json.loads(dj))
                except Exception:
                    pass
            rows_msg = conn.execute("SELECT origem, hora, texto FROM mensagens ORDER BY id DESC LIMIT 150").fetchall()
            for orig, hr, txt in reversed(rows_msg):
                mensagens.append({"origem": orig, "hora": hr, "texto": txt})
            rows_ret = conn.execute("SELECT hora, ocorrencia_id, texto FROM retorno_bravo ORDER BY id DESC LIMIT 30").fetchall()
            for hr, oid, txt in reversed(rows_ret):
                retornos.append({"hora": hr, "ocorrencia_id": oid, "texto": txt})
    except Exception as exc:
        print(f"[GUARDIAO] Erro ao carregar SQLite: {exc}", flush=True)
    return historico, mensagens, retornos


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


def _remover_modelo_404(modelo: str, ver: str):
    _MODELOS_INEXISTENTES_404.add((modelo, ver))
    if _MODELOS_CACHE["lista"]:
        _MODELOS_CACHE["lista"] = [
            item for item in _MODELOS_CACHE["lista"]
            if item != (modelo, ver)
        ]


def agora_br() -> datetime:
    return datetime.now(TZ_BR)


def obter_endereco_ocorrencia(indice_zero: int, nome_arquivo: str) -> str:
    fixo = os.environ.get("GUARDIAO_ENDERECO_PADRAO", "").strip()
    if fixo:
        return fixo
    base_idx = sum(ord(c) for c in (nome_arquivo or "")) + max(0, indice_zero)
    return ENDERECOS_DF_OPERACIONAIS[base_idx % len(ENDERECOS_DF_OPERACIONAIS)]


def _eh_item_reduzido(nome_arquivo: str) -> bool:
    n = (nome_arquivo or "").lower()
    return any(t in n for t in TERMOS_REDUCAO_50)


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

    filtradas = []
    contador_reduzidos = 0
    for arq in encontradas:
        if _eh_item_reduzido(arq.name):
            contador_reduzidos += 1
            if contador_reduzidos % 2 == 0:
                continue
        filtradas.append(arq)

    return filtradas or encontradas


_HIST_INICIAL, _MSGS_INICIAL, _RET_INICIAL = carregar_estado_db()
_ATIVAS_INICIAIS = [o for o in _HIST_INICIAL if o.get("situacao_fluxo") != "ARQUIVADA"]

STATE = {
    "indice": -1,
    "atual": _ATIVAS_INICIAIS[-1] if _ATIVAS_INICIAIS else None,
    "pausado": bool(_ATIVAS_INICIAIS[-1].get("acompanhando")) if _ATIVAS_INICIAIS else False,
    "ultima_troca": time.time() if _ATIVAS_INICIAIS else 0.0,
    "historico": _HIST_INICIAL[-300:],
    "mensagens": _MSGS_INICIAL or [
        {
            "origem": "CENTRAL",
            "hora": agora_br().strftime("%H:%M:%S"),
            "texto": "Monitoramento visual autonomo ativo."
        }
    ],
    "retorno_bravo": _RET_INICIAL or [
        {
            "hora": agora_br().strftime("%H:%M:%S"),
            "ocorrencia_id": "SISTEMA",
            "texto": "Aguardando despacho do ALFA para a equipe BRAVO."
        }
    ]
}

_TAREFAS_ACOMP = {}


def _atualizar_ocorrencia_por_id(oc_id: str, fn):
    for o in STATE["historico"]:
        if o["id"] == oc_id:
            fn(o)
            salvar_ocorrencia_db(o)
    if STATE["atual"] and STATE["atual"]["id"] == oc_id:
        fn(STATE["atual"])
        salvar_ocorrencia_db(STATE["atual"])


def _buscar_ocorrencia(oc_id: str):
    if STATE["atual"] and STATE["atual"].get("id") == oc_id:
        return STATE["atual"]
    for o in reversed(STATE["historico"]):
        if o.get("id") == oc_id:
            return o
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT dados_json FROM ocorrencias WHERE id = ?", (oc_id,)).fetchone()
            if row:
                return json.loads(row[0])
    except Exception:
        pass
    return STATE["atual"]


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
            otimizada.thumbnail((720, 720))
            buf = BytesIO()
            otimizada.save(buf, format="JPEG", quality=75, optimize=True)
            b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        try:
            b64_data = base64.b64encode(caminho.read_bytes()).decode("utf-8")
        except Exception:
            b64_data = ""

    if brilho < 55:
        obs_cena.append("Baixa iluminacao")
    elif brilho > 215:
        obs_cena.append("Alta iluminacao")
    else:
        obs_cena.append("Iluminacao regular")

    detalhes = " | ".join(obs_cena)
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


def _prioridade_modelo(nome: str) -> int:
    n = (nome or "").lower()
    if n == "gemini-flash-lite-latest":
        return 0
    if "flash-lite" in n and "preview" not in n:
        return 1
    if "flash-lite" in n:
        return 2
    if n == "gemini-flash-latest":
        return 3
    if "flash" in n and "lite" not in n and "preview" not in n:
        return 4
    if "flash" in n:
        return 5
    return 6


def obter_modelos_disponiveis(chave: str):
    agora = time.time()
    if (
        _MODELOS_CACHE["lista"]
        and _MODELOS_CACHE["chave"] == chave
        and agora < _MODELOS_CACHE["expira"]
    ):
        return [p for p in _MODELOS_CACHE["lista"] if p not in _MODELOS_INEXISTENTES_404]

    descobertos = []
    if chave:
        for ver in ("v1beta", "v1"):
            try:
                r = requests.get(
                    f"https://generativelanguage.googleapis.com/{ver}/models?key={chave}",
                    timeout=10
                )
                if r.status_code == 200:
                    for item in (r.json().get("models") or []):
                        nome = str(item.get("name") or "").replace("models/", "").strip()
                        metodos = item.get("supportedGenerationMethods") or []
                        if nome and "generateContent" in metodos:
                            if "gemini" in nome.lower() and not _modelo_bloqueado(nome):
                                par = (nome, ver)
                                if par not in descobertos and par not in _MODELOS_INEXISTENTES_404:
                                    descobertos.append(par)
                    if descobertos:
                        break
            except Exception as exc:
                print(f"[GUARDIAO] Erro ao listar modelos ({ver}): {type(exc).__name__} — {exc}", flush=True)

    descobertos.sort(key=lambda item: _prioridade_modelo(item[0]))

    modelo_env = obter_modelo_env()
    prioritarios = []

    if modelo_env and modelo_env != "gemini-2.5-flash":
        for ver in ("v1beta", "v1"):
            par = (modelo_env, ver)
            if par not in prioritarios and par not in _MODELOS_INEXISTENTES_404:
                prioritarios.append(par)

    for par in descobertos:
        if par not in prioritarios and par not in _MODELOS_INEXISTENTES_404:
            prioritarios.append(par)

    for m_pref in (
        "gemini-flash-lite-latest",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-lite",
        "gemini-flash-latest",
        "gemini-2.5-flash",
        "gemini-2.0-flash"
    ):
        for ver in ("v1beta", "v1"):
            par = (m_pref, ver)
            if par not in prioritarios and par not in _MODELOS_INEXISTENTES_404:
                prioritarios.append(par)

    fila_final = prioritarios[:12]
    print(f"[GUARDIAO] Fila de modelos estaveis ativa: {fila_final}", flush=True)
    _MODELOS_CACHE["lista"] = fila_final
    _MODELOS_CACHE["expira"] = agora + 1800.0
    _MODELOS_CACHE["chave"] = chave
    return fila_final


def analise_visual_fallback(info: dict, motivo: str = ""):
    objetos = []
    pontos = []
    razao = info.get("razao", 1.0)
    bordas = info.get("bordas", 0.0)
    contraste = info.get("contraste", 0.0)

    if razao >= 2.2 and bordas >= 15.0 and contraste >= 25.0:
        conf = round(min(92.0, max(65.0, 60.0 + bordas * 0.4 + contraste * 0.15)), 1)
        objetos.append({
            "rotulo": "Objeto alongado",
            "confianca": f"{conf}%",
            "detalhe": ""
        })
        nivel = "OBSERVACAO"
        status = "OBSERVACAO_VISUAL"
        sintese = "Objeto alongado na cena; conferir natureza do item."
        relato = "Ha um elemento alongado em destaque na cena para verificacao visual."
        pontos.append("Confirmar tipo de objeto")
    elif bordas >= 16.0:
        conf = round(min(86.0, max(58.0, 55.0 + bordas * 0.35)), 1)
        objetos.append({
            "rotulo": "Elemento definido",
            "confianca": f"{conf}%",
            "detalhe": ""
        })
        nivel = "NORMAL"
        status = "REGISTRO_VISUAL"
        sintese = "Cena sem ameaca imediata identificada."
        relato = "Elementos visiveis sem indicio conclusivo de armamento ou hostilidade."
    else:
        nivel = "NORMAL"
        status = "SEM_ANOMALIA_APARENTE"
        sintese = "Sem anomalias evidentes na cena."
        relato = "Sem indicios visuais de armamento ou risco imediato."

    return {
        "modo_analise": "SENSOR_VISUAL",
        "status": status,
        "nivel": nivel,
        "pessoas_visiveis": 0,
        "objetos": objetos,
        "sintese_operacional": sintese,
        "relato_visual": relato,
        "pontos_atencao": pontos or ["Manter vigilancia"],
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
        "Voce e o modulo de apoio decisorio do comando (ALFA). "
        "Escreva em portugues curto, direto e sem repeticoes — cada campo deve trazer informacao nova e nao repetir frases de outros campos. "
        "Nao mencione pixels, resolucao, brilho, contraste, sensores, IA, modelos ou tecnologia.\n"
        "Regras por campo:\n"
        "- 'sintese_operacional': 1 frase curta com o fato critico e a conduta imediata do ALFA.\n"
        "- 'objetos': liste apenas os itens/armas distintos sem repetir explicacoes longas.\n"
        "- 'relato_detalhado': descreva de forma objetiva apenas o que se ve (pessoas, vestimenta/farda, postura/ferimento, arma e posicao).\n"
        "- 'pontos_atencao': ate 3 itens curtos com condutas ou riscos especificos nao repetidos.\n"
        "- Se houver arma de fogo ou arma branca visivel (no coldre, empunhada ou isolada), informe 'caixa_arma' como [ymin, xmin, ymax, xmax] de 0 a 1000.\n"
        "Responda APENAS com JSON valido:\n"
        "{\n"
        '  "arma_fogo": bool,\n'
        '  "arma_branca": bool,\n'
        '  "atitude_ameaca": bool,\n'
        '  "objeto_na_mao": "string ou vazio",\n'
        '  "pessoas_visiveis": int,\n'
        '  "nivel_sugerido": "NORMAL" | "OBSERVACAO" | "ATENCAO_ELEVADA" | "ALTO_RISCO",\n'
        '  "objetos": [{"rotulo": "string", "confianca": 0-100, "detalhe": "string"}],\n'
        '  "sintese_operacional": "string curta",\n'
        '  "relato_detalhado": "string objetiva",\n'
        '  "pontos_atencao": ["string"],\n'
        '  "caixa_arma": [ymin, xmin, ymax, xmax] ou null\n'
        "}"
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
    fila = obter_modelos_disponiveis(chave)
    for modelo, ver in fila:
        url = f"https://generativelanguage.googleapis.com/{ver}/models/{modelo}:generateContent?key={chave}"
        try:
            resp = requests.post(url, json=payload, timeout=18)
            if resp.status_code == 404:
                _remover_modelo_404(modelo, ver)
                ultimo_erro = f"Modelo {modelo} inexistente (404)"
                print(f"[GUARDIAO] {ultimo_erro} em {ver}", flush=True)
                continue
            if resp.status_code in (400, 401, 403):
                msg = f"Falha de autorizacao/chave (HTTP {resp.status_code})"
                print(f"[GUARDIAO] {msg} em {modelo} ({ver}): {resp.text[:180]}", flush=True)
                return None, msg
            if resp.status_code in (429, 500, 502, 503, 504):
                ultimo_erro = f"Instabilidade temporaria (HTTP {resp.status_code}) em {modelo}"
                print(f"[GUARDIAO] {ultimo_erro} ({ver}) — pulando para proximo modelo", flush=True)
                time.sleep(0.5)
                continue
            resp.raise_for_status()
            data = resp.json()
            candidatos = data.get("candidates") or []
            if not candidatos:
                ultimo_erro = f"Sem candidatos em {modelo}"
                continue
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
        except requests.exceptions.Timeout:
            ultimo_erro = f"Timeout em {modelo} ({ver})"
            print(f"[GUARDIAO] {ultimo_erro} — pulando para proximo modelo", flush=True)
            continue
        except Exception as exc:
            ultimo_erro = f"Falha em {modelo}: {type(exc).__name__}"
            print(f"[GUARDIAO] {ultimo_erro} ({ver}): {exc}", flush=True)
            continue

    print(f"[GUARDIAO] Fallback acionado: {ultimo_erro}", flush=True)
    return None, ultimo_erro


async def consultar_visao_externa(mime: str, img_b64: str):
    return await asyncio.to_thread(_chamar_visao_sync, mime, img_b64)


def sanitizar_texto_operacional(txt: str) -> str:
    if not txt:
        return ""
    limpo = re.sub(
        r"(?i)\b(guardiao\s*df-?360|guardiao|sspdf|gemini|google\s*ai\s*studio|generativelanguage|render|gemini_api_key|guardiao_chave_visao|sem_chave_ia_configurada)\b",
        "monitoramento",
        txt
    )
    return re.sub(r"\s{2,}", " ", limpo).strip()


def _deduplicar_lista(itens):
    resultado = []
    vistos = set()
    for item in itens:
        s = sanitizar_texto_operacional(str(item or "").strip())
        if not s or s.lower() in ("nenhum", "nenhuma", "nada", "vazio"):
            continue
        chave = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
        if not chave or chave in vistos or any(chave in v or v in chave for v in vistos if len(chave) > 12 and len(v) > 12):
            continue
        vistos.add(chave)
        resultado.append(s)
    return resultado


async def analisar_imagem_ia(caminho: Path):
    info = await asyncio.to_thread(inspecionar_imagem, caminho)
    parsed, motivo_erro = await consultar_visao_externa(info["mime"], info["b64"])

    if not parsed:
        return analise_visual_fallback(info, motivo_erro)

    arma_fogo = bool(parsed.get("arma_fogo", False))
    arma_branca = bool(parsed.get("arma_branca", False))
    atitude_ameaca = bool(parsed.get("atitude_ameaca", False))
    objeto_mao = sanitizar_texto_operacional(str(parsed.get("objeto_na_mao") or "").strip())
    if objeto_mao.lower() in ("nenhum", "nenhuma", "nao ha", "não há", "vazio", "nada"):
        objeto_mao = ""

    try:
        pessoas = max(0, int(parsed.get("pessoas_visiveis", 0) or 0))
    except Exception:
        pessoas = 0

    objetos = []
    rotulos_vistos = set()
    candidatos_obj = []
    if objeto_mao:
        candidatos_obj.append((objeto_mao, "", ""))
    if isinstance(parsed.get("objetos"), list):
        for item in parsed.get("objetos"):
            if isinstance(item, dict):
                rot = sanitizar_texto_operacional(str(item.get("rotulo") or item.get("classe") or ""))
                det = sanitizar_texto_operacional(str(item.get("detalhe") or ""))
                candidatos_obj.append((rot, det, item.get("confianca")))

    for rot, det, _ in candidatos_obj:
        if not rot or rot.lower() in ("nenhum", "nenhuma", "nada", "vazio"):
            continue
        chave_rot = re.sub(r"[^a-z0-9]+", "", rot.lower())
        if not chave_rot or chave_rot in rotulos_vistos:
            continue
        rotulos_vistos.add(chave_rot)
        det_limpo = det if det and det.lower() not in ("em posse / na cena", rot.lower()) else ""
        objetos.append({"rotulo": rot, "confianca": "", "detalhe": det_limpo})

    relato = sanitizar_texto_operacional(
        str(parsed.get("relato_detalhado") or parsed.get("relato") or "Analise visual concluida.")
    )
    sintese = sanitizar_texto_operacional(str(parsed.get("sintese_operacional") or ""))
    if not sintese:
        sintese = relato[:180]

    raw_pontos = []
    if atitude_ameaca:
        raw_pontos.append("Risco ou atitude hostil na cena")
    if isinstance(parsed.get("pontos_atencao"), list):
        raw_pontos.extend(parsed.get("pontos_atencao"))
    pontos = _deduplicar_lista(raw_pontos)[:3]
    if not pontos:
        pontos = ["Acompanhamento em curso"]

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


def _registrar_ocorrencia(arq: Path, indice_zero: int, total: int, analise: dict):
    agora = agora_br()
    endereco = obter_endereco_ocorrencia(indice_zero, arq.name)
    oc = {
        "id": f"OC-{agora.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4].upper()}",
        "iso_dt": agora.isoformat(),
        "data_hora": agora.strftime("%d/%m/%Y %H:%M:%S"),
        "endereco": endereco,
        "indice": indice_zero + 1,
        "total": total,
        "arquivo": arq.name,
        "caminho": str(arq),
        "situacao_fluxo": "ATIVA",
        "etapa_retorno": 0,
        "acompanhando": False,
        "solucao_pronta": False,
        "aviso_solucao": "",
        "unidade_bravo": "",
        "retorno_bravo": "",
        "codigo_sessao": uuid.uuid4().hex[:8].upper(),
        **analise
    }
    STATE["atual"] = oc
    STATE["ultima_troca"] = time.time()
    STATE["historico"].append(oc)
    STATE["historico"] = STATE["historico"][-300:]
    salvar_ocorrencia_db(oc)

    hora_msg = agora.strftime("%H:%M:%S")
    txt_msg = f"[{oc['id']}] {oc['nivel']} — {endereco} — {oc['sintese_operacional']}"
    STATE["mensagens"].append({"origem": "CENTRAL", "hora": hora_msg, "texto": txt_msg})
    STATE["mensagens"] = STATE["mensagens"][-150:]
    salvar_mensagem_db("CENTRAL", hora_msg, txt_msg)
    return oc


async def avancar_ciclo():
    imagens = listar_imagens()
    if not imagens:
        return
    STATE["indice"] = (STATE["indice"] + 1) % len(imagens)
    arq = imagens[STATE["indice"]]
    try:
        analise = await analisar_imagem_ia(arq)
    except Exception as exc:
        print(f"[GUARDIAO] Erro inesperado em {arq.name}: {type(exc).__name__} — {exc}", flush=True)
        info = await asyncio.to_thread(inspecionar_imagem, arq)
        analise = analise_visual_fallback(info, f"Falha inesperada: {type(exc).__name__}")

    _registrar_ocorrencia(arq, STATE["indice"], len(imagens), analise)


async def voltar_ocorrencia_anterior():
    ativas = [o for o in STATE["historico"] if o.get("situacao_fluxo") != "ARQUIVADA"]
    atual_id = STATE["atual"]["id"] if STATE["atual"] else ""
    if len(ativas) >= 2:
        idx_atual = next((i for i, o in enumerate(ativas) if o["id"] == atual_id), len(ativas) - 1)
        idx_prev = idx_atual - 1 if idx_atual > 0 else len(ativas) - 1
        anterior = dict(ativas[idx_prev])
        if not anterior.get("endereco"):
            anterior["endereco"] = obter_endereco_ocorrencia(max(0, (anterior.get("indice") or 1) - 1), anterior.get("arquivo") or "")
        STATE["atual"] = anterior
        STATE["ultima_troca"] = time.time()
        return anterior

    imagens = listar_imagens()
    if not imagens:
        return STATE["atual"]
    STATE["indice"] = (STATE["indice"] - 1) % len(imagens)
    arq = imagens[STATE["indice"]]
    try:
        analise = await analisar_imagem_ia(arq)
    except Exception as exc:
        info = await asyncio.to_thread(inspecionar_imagem, arq)
        analise = analise_visual_fallback(info, f"Falha inesperada: {type(exc).__name__}")
    return _registrar_ocorrencia(arq, STATE["indice"], len(imagens), analise)


async def _monitorar_solucao_ate_aviso(oc_id: str, unidade: str, endereco: str):
    loc_txt = f" em {endereco}" if endereco else ""
    try:
        await asyncio.sleep(5)
        hora_etapa = agora_br().strftime("%H:%M:%S")
        txt_etapa = f"Unidade {unidade} em deslocamento{loc_txt}."
        STATE["retorno_bravo"].append({"hora": hora_etapa, "ocorrencia_id": oc_id, "texto": txt_etapa})
        STATE["retorno_bravo"] = STATE["retorno_bravo"][-30:]
        salvar_retorno_db(hora_etapa, oc_id, txt_etapa)
        _atualizar_ocorrencia_por_id(oc_id, lambda o: o.update({
            "retorno_bravo": f"[{hora_etapa}] {txt_etapa}",
            "etapa_retorno": 1,
            "solucao_pronta": False,
            "aviso_solucao": ""
        }))

        await asyncio.sleep(5)
        hora_med = agora_br().strftime("%H:%M:%S")
        txt_med = f"Unidade {unidade} no local{loc_txt}; averiguacao em andamento."
        STATE["retorno_bravo"].append({"hora": hora_med, "ocorrencia_id": oc_id, "texto": txt_med})
        STATE["retorno_bravo"] = STATE["retorno_bravo"][-30:]
        salvar_retorno_db(hora_med, oc_id, txt_med)
        _atualizar_ocorrencia_por_id(oc_id, lambda o: o.update({
            "retorno_bravo": f"[{hora_med}] {txt_med}",
            "etapa_retorno": 2,
            "solucao_pronta": False,
            "aviso_solucao": ""
        }))

        await asyncio.sleep(6)
        hora_sol = agora_br().strftime("%H:%M:%S")
        desfechos = [
            f"Unidade {unidade} concluiu a ocorrencia{loc_txt}; situacao controlada e solucionada.",
            f"Unidade {unidade} encerrou o atendimento{loc_txt} com local estabilizado e sem risco pendente."
        ]
        txt_sol = random.choice(desfechos)
        aviso_alfa = f"SOLUCAO INFORMADA POR {unidade} as {hora_sol}: Ocorrencia pronta para arquivamento pelo ALFA."
        STATE["retorno_bravo"].append({"hora": hora_sol, "ocorrencia_id": oc_id, "texto": txt_sol})
        STATE["retorno_bravo"] = STATE["retorno_bravo"][-30:]
        salvar_retorno_db(hora_sol, oc_id, txt_sol)
        _atualizar_ocorrencia_por_id(oc_id, lambda o: o.update({
            "retorno_bravo": f"[{hora_sol}] {txt_sol}",
            "etapa_retorno": 4,
            "solucao_pronta": True,
            "aviso_solucao": aviso_alfa
        }))
        STATE["mensagens"].append({"origem": unidade, "hora": hora_sol, "texto": aviso_alfa})
        STATE["mensagens"] = STATE["mensagens"][-150:]
        salvar_mensagem_db(unidade, hora_sol, aviso_alfa)
    except asyncio.CancelledError:
        pass


def _iniciar_monitoramento_solucao(oc_id: str, unidade: str, endereco: str):
    tarefa_ant = _TAREFAS_ACOMP.get(oc_id)
    if tarefa_ant and not tarefa_ant.done():
        tarefa_ant.cancel()
    _TAREFAS_ACOMP[oc_id] = asyncio.create_task(_monitorar_solucao_ate_aviso(oc_id, unidade, endereco))


def _cancelar_monitoramento_solucao(oc_id: str):
    tarefa = _TAREFAS_ACOMP.pop(oc_id, None)
    if tarefa and not tarefa.done():
        tarefa.cancel()


async def simular_retorno_bravo_demo(oc_id: str, unidade: str = "BRAVO", endereco: str = ""):
    if not MODO_DEMO_RETORNO_BRAVO:
        return
    await asyncio.sleep(6)
    hora = agora_br().strftime("%H:%M:%S")
    loc_txt = f" em {endereco}" if endereco else ""
    relatos = [
        (1, f"Unidade {unidade} em deslocamento{loc_txt}."),
        (2, f"Unidade {unidade} no local{loc_txt}; averiguacao em andamento."),
        (2, f"Unidade {unidade} concluiu a averiguacao inicial{loc_txt} e aguarda orientacao do ALFA.")
    ]
    etapa_sorteada, txt = random.choice(relatos)
    STATE["retorno_bravo"].append({"hora": hora, "ocorrencia_id": oc_id, "texto": txt})
    STATE["retorno_bravo"] = STATE["retorno_bravo"][-30:]
    salvar_retorno_db(hora, oc_id, txt)
    _atualizar_ocorrencia_por_id(oc_id, lambda o: o.update({
        "retorno_bravo": f"[{hora}] {txt}",
        "etapa_retorno": max(int(o.get("etapa_retorno") or 0), etapa_sorteada) if not o.get("solucao_pronta") else 4
    }))


async def loop_autonomo():
    if STATE["atual"] is None:
        try:
            await avancar_ciclo()
        except Exception as exc:
            print(f"[GUARDIAO] Falha no ciclo inicial: {type(exc).__name__} — {exc}", flush=True)
            STATE["ultima_troca"] = time.time()
    while True:
        await asyncio.sleep(1)
        if not STATE["pausado"] and (time.time() - STATE["ultima_troca"]) >= INTERVALO_SEG:
            try:
                await avancar_ciclo()
            except Exception as exc:
                print(f"[GUARDIAO] Falha no loop autonomo: {type(exc).__name__} — {exc}", flush=True)
                STATE["ultima_troca"] = time.time()


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(loop_autonomo())


@app.get("/imagem")
async def get_imagem(oc_id: str = ""):
    oc = _buscar_ocorrencia(oc_id) if oc_id else STATE["atual"]
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


@app.get("/api/relatorio-bo")
async def api_relatorio_bo(oc_id: str = ""):
    oc = _buscar_ocorrencia(oc_id) if oc_id else STATE["atual"]
    if not oc:
        return HTMLResponse("<h2>Nenhuma ocorrencia disponivel para emissao de relatorio.</h2>", status_code=404)

    emissao = agora_br().strftime("%d/%m/%Y %H:%M:%S")
    objs = ", ".join(
        f"{x.get('rotulo', '')}{(' (' + x.get('detalhe') + ')') if x.get('detalhe') else ''}"
        for x in (oc.get("objetos") or []) if x.get("rotulo")
    ) or "Nenhum item relevante"
    pontos = " | ".join(oc.get("pontos_atencao") or []) or "Sem pontos adicionais"
    img_url = f"/imagem?oc_id={oc.get('id', '')}"

    def h(v):
        return (
            str(v or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    html_bo = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>RELATORIO TECNICO DE OCORRENCIA - {h(oc.get('id'))}</title>
<style>
  body{{font-family:Arial,Helvetica,sans-serif;color:#111827;margin:24px;line-height:1.45;font-size:13px}}
  .topo{{border-bottom:2px solid #1e3a8a;padding-bottom:10px;margin-bottom:16px;display:flex;justify-content:space-between;align-items:flex-end}}
  .topo h1{{margin:0;font-size:18px;color:#0f172a}}
  .sub{{font-size:11px;color:#475569}}
  .sec{{border:1px solid #cbd5e1;border-radius:6px;padding:12px;margin-bottom:12px}}
  .sec h2{{margin:0 0 8px 0;font-size:13px;color:#1e3a8a;border-bottom:1px solid #e2e8f0;padding-bottom:4px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
  .campo b{{color:#0f172a}}
  .foto-bo{{max-width:100%;max-height:280px;border:1px solid #94a3b8;border-radius:4px;display:block;margin-top:6px}}
  .acoes{{margin-bottom:14px}}
  .btn-print{{background:#1d4ed8;color:#fff;border:none;padding:9px 14px;border-radius:6px;font-weight:bold;cursor:pointer;font-size:13px}}
  @media print{{.acoes{{display:none}} body{{margin:10px}}}}
</style>
</head>
<body>
  <div class="acoes">
    <button class="btn-print" onclick="window.print()">SALVAR EM PDF / IMPRIMIR COMPROVANTE</button>
  </div>
  <div class="topo">
    <div>
      <h1>RELATORIO TECNICO OPERACIONAL (BO / AUDITORIA)</h1>
      <div class="sub">Registro oficial emitido pelo Painel de Monitoramento Operacional</div>
    </div>
    <div style="text-align:right;font-size:11px">
      <b>Protocolo:</b> {h(oc.get('id'))}<br>
      <b>Emitido em:</b> {h(emissao)}
    </div>
  </div>
  <div class="sec">
    <h2>1. IDENTIFICACAO E LOCALIZACAO</h2>
    <div class="grid">
      <div class="campo"><b>Data/Hora do Registro:</b> {h(oc.get('data_hora'))}</div>
      <div class="campo"><b>Situacao Atual:</b> {h(oc.get('situacao_fluxo'))}</div>
      <div class="campo"><b>Nivel de Risco:</b> {h(oc.get('nivel'))}</div>
      <div class="campo"><b>Classificacao:</b> {h(oc.get('status'))}</div>
      <div class="campo" style="grid-column:1/-1"><b>Endereco / Local:</b> {h(oc.get('endereco'))}</div>
    </div>
  </div>
  <div class="sec">
    <h2>2. EVIDENCIA VISUAL REGISTRADA</h2>
    <img class="foto-bo" src="{h(img_url)}" alt="Evidencia visual">
    <div class="campo" style="margin-top:6px"><b>Arquivo de Referencia:</b> {h(oc.get('arquivo'))} | <b>Pessoas Visiveis:</b> {h(oc.get('pessoas_visiveis', 0))}</div>
  </div>
  <div class="sec">
    <h2>3. ANALISE SITUACIONAL</h2>
    <div class="campo"><b>Sintese Operacional (ALFA):</b> {h(oc.get('sintese_operacional'))}</div>
    <div class="campo" style="margin-top:6px"><b>Elementos Identificados:</b> {h(objs)}</div>
    <div class="campo" style="margin-top:6px"><b>Relato Objetivo:</b> {h(oc.get('relato_visual'))}</div>
    <div class="campo" style="margin-top:6px"><b>Pontos de Atencao:</b> {h(pontos)}</div>
    <div class="campo" style="margin-top:6px"><b>Condicao do Local:</b> {h(oc.get('detalhes_cena'))}</div>
  </div>
  <div class="sec">
    <h2>4. DESPACHO E DESFECHO OPERACIONAL</h2>
    <div class="campo"><b>Despacho de Unidade:</b> {h(oc.get('unidade_bravo') or 'Nao despachado')}</div>
    <div class="campo" style="margin-top:6px"><b>Retorno da Equipe:</b> {h(oc.get('retorno_bravo') or 'Sem retorno registrado')}</div>
    <div class="campo" style="margin-top:6px"><b>Status de Solucao:</b> {h(oc.get('aviso_solucao') or ('Solucao confirmada' if oc.get('solucao_pronta') else 'Em andamento / Aguardando'))}</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_bo)


@app.get("/api/estado")
async def api_estado():
    restante = max(0, int(INTERVALO_SEG - (time.time() - STATE["ultima_troca"]))) if not STATE["pausado"] else int(INTERVALO_SEG)
    agora = agora_br()
    return {
        "agora": agora.strftime("%d/%m/%Y — %H:%M:%S"),
        "restante": restante,
        "pausado": STATE["pausado"],
        "modo_demo_retorno": MODO_DEMO_RETORNO_BRAVO,
        "total_persistido": len(STATE["historico"]),
        "atual": STATE["atual"],
        "mensagens": STATE["mensagens"][-40:],
        "retorno_bravo": STATE["retorno_bravo"][-30:]
    }


@app.post("/api/enviar-bravo")
async def enviar_bravo(req: Request):
    dados = await req.json()
    oc_id = dados.get("id") or (STATE["atual"]["id"] if STATE["atual"] else "N/A")
    unidade = (dados.get("unidade") or "BRAVO-01").strip()
    hora = agora_br().strftime("%H:%M:%S")
    endereco = (STATE["atual"].get("endereco") if STATE["atual"] else "") or obter_endereco_ocorrencia(0, oc_id)
    envio_txt = f"ENVIADO PARA {unidade} as {hora} — Local: {endereco}"
    _atualizar_ocorrencia_por_id(oc_id, lambda o: o.update({
        "situacao_fluxo": f"ENVIADO ({unidade})",
        "unidade_bravo": envio_txt,
        "etapa_retorno": 1 if not o.get("solucao_pronta") else 4,
        "solucao_pronta": False,
        "aviso_solucao": ""
    }))
    txt_m = f"Ocorrencia {oc_id} enviada para {unidade} ({endereco})."
    STATE["mensagens"].append({"origem": f"ALFA -> {unidade}", "hora": hora, "texto": txt_m})
    STATE["mensagens"] = STATE["mensagens"][-150:]
    salvar_mensagem_db(f"ALFA -> {unidade}", hora, txt_m)
    if MODO_DEMO_RETORNO_BRAVO:
        asyncio.create_task(simular_retorno_bravo_demo(oc_id, unidade, endereco))
    return {"ok": True, "unidade": unidade, "endereco": endereco, "envio": envio_txt}


@app.post("/api/acompanhar")
async def acompanhar_ocorrencia(req: Request):
    dados = await req.json()
    oc_id = dados.get("id") or (STATE["atual"]["id"] if STATE["atual"] else "")
    unidade = (dados.get("unidade") or (STATE["atual"].get("unidade_bravo") if STATE["atual"] else "") or "BRAVO-01").strip()
    if "PARA " in unidade and " as " in unidade:
        unidade = unidade.split("PARA ", 1)[1].split(" as ", 1)[0].strip()
    endereco = (STATE["atual"].get("endereco") if STATE["atual"] else "") or obter_endereco_ocorrencia(0, oc_id)
    hora = agora_br().strftime("%H:%M:%S")
    acompanhando = True
    for o in STATE["historico"]:
        if o["id"] == oc_id:
            acompanhando = not bool(o.get("acompanhando"))
            break
    if acompanhando:
        _atualizar_ocorrencia_por_id(oc_id, lambda o: o.update({
            "acompanhando": True,
            "situacao_fluxo": "EM ACOMPANHAMENTO",
            "etapa_retorno": 3 if not o.get("solucao_pronta") else 4,
            "solucao_pronta": False,
            "aviso_solucao": ""
        }))
        STATE["pausado"] = True
        if MODO_DEMO_RETORNO_BRAVO:
            _iniciar_monitoramento_solucao(oc_id, unidade, endereco)
    else:
        _cancelar_monitoramento_solucao(oc_id)
        _atualizar_ocorrencia_por_id(oc_id, lambda o: o.update({
            "acompanhando": False,
            "situacao_fluxo": "ATIVA",
            "etapa_retorno": 0,
            "solucao_pronta": False,
            "aviso_solucao": ""
        }))
        STATE["pausado"] = False
        STATE["ultima_troca"] = time.time()
    txt_m = f"Ocorrencia {oc_id} {'em acompanhamento ate a solucao' if acompanhando else 'liberada do acompanhamento'}."
    STATE["mensagens"].append({"origem": "ALFA", "hora": hora, "texto": txt_m})
    STATE["mensagens"] = STATE["mensagens"][-150:]
    salvar_mensagem_db("ALFA", hora, txt_m)
    return {"ok": True, "acompanhando": acompanhando, "pausado": STATE["pausado"]}


@app.post("/api/ocorrencia-anterior")
async def ocorrencia_anterior():
    oc = await voltar_ocorrencia_anterior()
    return {"ok": True, "atual": oc}


@app.post("/api/retorno-bravo")
async def retorno_bravo(req: Request):
    dados = await req.json()
    texto = (dados.get("texto") or "").strip()
    oc_id = (dados.get("oc_id") or (STATE["atual"]["id"] if STATE["atual"] else "GERAL")).strip()
    solucao = bool(dados.get("solucao", False)) or bool(re.search(r"(?i)\b(solucionad|concluid|encerrad|resolvida|finalizad)\b", texto))
    if texto:
        hora = agora_br().strftime("%H:%M:%S")
        STATE["retorno_bravo"].append({"hora": hora, "ocorrencia_id": oc_id, "texto": texto})
        STATE["retorno_bravo"] = STATE["retorno_bravo"][-30:]
        salvar_retorno_db(hora, oc_id, texto)
        aviso = f"SOLUCAO INFORMADA as {hora}: {texto} — Pronta para arquivamento pelo ALFA." if solucao else ""
        etapa_novo = 4 if solucao else (2 if re.search(r"(?i)\b(local|averigu|aguarda)\b", texto) else 1)
        _atualizar_ocorrencia_por_id(oc_id, lambda o: o.update({
            "retorno_bravo": f"[{hora}] {texto}",
            "etapa_retorno": 4 if (solucao or bool(o.get("solucao_pronta"))) else max(int(o.get("etapa_retorno") or 0), etapa_novo),
            "solucao_pronta": solucao or bool(o.get("solucao_pronta")),
            "aviso_solucao": aviso or o.get("aviso_solucao", "")
        }))
    return {"ok": True}


@app.post("/api/arquivar")
async def arquivar(req: Request):
    dados = await req.json()
    oc_id = dados.get("id") or (STATE["atual"]["id"] if STATE["atual"] else "")
    _cancelar_monitoramento_solucao(oc_id)
    _atualizar_ocorrencia_por_id(oc_id, lambda o: o.update({
        "situacao_fluxo": "ARQUIVADA",
        "acompanhando": False
    }))
    STATE["pausado"] = False
    STATE["atual"] = None
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
                salvar_ocorrencia_db(o)
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
        STATE["mensagens"].append({"origem": "CENTRAL", "hora": hora, "texto": f"Registrado: '{texto}'."})
        STATE["mensagens"] = STATE["mensagens"][-150:]
        salvar_mensagem_db("ALFA", hora, texto)
        salvar_mensagem_db("CENTRAL", hora, f"Registrado: '{texto}'.")
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
<title>PAINEL OPERACIONAL</title>
<style>
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;width:100%;max-width:100%;overflow-x:clip;background:#090d16;color:#e6eef8;font-family:Segoe UI,sans-serif}
header{background:#0d2545;padding:10px 14px;display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;border-bottom:2px solid #2563eb;width:100%}
header>div{min-width:0;max-width:100%;overflow-wrap:anywhere}
.relogio{font-family:monospace;font-size:15px;color:#7dd3fc;font-weight:bold;white-space:nowrap}
.wrap{max-width:980px;margin:0 auto;padding:12px;width:100%}
.card{background:#111827;border:1px solid #243247;border-radius:8px;padding:12px;margin-bottom:12px;min-width:0;max-width:100%;overflow:hidden}
.card h3{margin:0 0 8px 0;color:#93c5fd;font-size:13px;overflow-wrap:anywhere}
img.foto{max-width:100%;width:auto;max-height:310px;border-radius:6px;display:block;margin:0;background:#000}
.endereco-bar{margin-top:8px;padding:7px 10px;border-radius:6px;background:#0f172a;border:1px solid #1e3a8a;color:#bae6fd;font-size:12px;overflow-wrap:anywhere}
.acomp-bar{margin-top:8px;padding:8px 10px;border-radius:6px;background:#064e3b;border:1px solid #34d399;color:#d1fae5;font-size:12px;font-weight:700;overflow-wrap:anywhere}
.solucao-bar{margin-top:8px;padding:11px 13px;border-radius:6px;background:#14532d;border:2px solid #ffffff;color:#ffffff;font-size:13px;font-weight:800;line-height:1.4;overflow-wrap:anywhere}
.botoes{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;align-items:center;max-width:100%}
button{padding:7px 11px;border:none;border-radius:6px;cursor:pointer;font-weight:600;color:#fff;font-size:12px;max-width:100%}
.b-bravo{background:#dc2626}.b-arq{background:#2563eb}.b-desarq{background:#059669}.b-prev{background:#334155}.b-acomp{background:#d97706}.b-acomp-ativo{background:#047857}.b-bo{background:#4f46e5}
select,input{background:#0b101b;color:#fff;border:1px solid #334155;border-radius:6px;padding:6px 8px;font-size:12px;min-width:0;max-width:100%}
.box{background:#0b101b;border:1px solid #1e293b;border-radius:6px;padding:8px;max-height:260px;overflow-y:auto;overflow-x:hidden;font-size:12px;overflow-wrap:anywhere;word-break:break-word;max-width:100%}
.linha-rel{margin-top:5px;line-height:1.4}
.crit{color:#fca5a5}.med{color:#fcd34d}.seg{color:#86efac}
.envio-ok{margin-top:8px;padding:7px 10px;border-radius:6px;background:#064e3b;border:1px solid #10b981;color:#a7f3d0;font-size:12px;font-weight:700}
.retorno-destaque{margin-top:8px;padding:10px 12px;border-radius:6px;background:#451a03;border:2px solid #f59e0b;color:#fde68a;font-size:13px;font-weight:700;line-height:1.35;overflow-wrap:anywhere}
.ret-e1{background:#172554;border-color:#60a5fa;color:#dbeafe}
.ret-e2{background:#451a03;border-color:#f59e0b;color:#fde68a}
.ret-e3{background:#3b0764;border-color:#c084fc;color:#f3e8ff}
.ret-e4{background:#14532d;border-color:#ffffff;color:#ffffff}
.selo-etapa{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:800;letter-spacing:0.3px;margin-right:6px;vertical-align:middle}
.selo-e1{background:#1d4ed8;color:#ffffff}
.selo-e2{background:#d97706;color:#ffffff}
.selo-e3{background:#7e22ce;color:#ffffff}
.selo-e4{background:#ffffff;color:#052e16}
</style>
</head>
<body>
<header>
  <div><b>PAINEL OPERACIONAL</b> | Ciclo: <span id="ciclo">02:00</span> | Banco: <span id="dbCount">0</span></div>
  <div class="relogio" id="relogio">--/--/---- --:--:--</div>
</header>
<div class="wrap">
  <div class="card">
    <h3>CAMPO DE VISAO</h3>
    <img id="foto" class="foto" src="/imagem" alt="Imagem analisada">
    <div id="meta" style="font-size:11px;color:#94a3b8;margin-top:6px;overflow-wrap:anywhere"></div>
    <div id="enderecoBox" class="endereco-bar" style="display:none"></div>
    <div id="acompBox" class="acomp-bar" style="display:none"></div>
    <div id="statusEnvio" class="envio-ok" style="display:none"></div>
    <div id="retornoDestaque" class="retorno-destaque" style="display:none"></div>
    <div id="solucaoBox" class="solucao-bar" style="display:none"></div>
    <div class="botoes">
      <select id="selUnidade">
        <option value="BRAVO-01">BRAVO-01</option>
        <option value="BRAVO-02">BRAVO-02</option>
        <option value="BRAVO-03">BRAVO-03</option>
        <option value="ROTAM">ROTAM</option>
        <option value="BPCHOQUE">BPCHOQUE</option>
        <option value="PMDF - APOIO">PMDF - APOIO</option>
      </select>
      <button class="b-bravo" onclick="enviarBravo()">ENVIAR BRAVO</button>
      <button id="btnAcompanhar" class="b-acomp" onclick="acompanharOcorrencia()">ACOMPANHAR OCORRENCIA</button>
      <button class="b-arq" onclick="arquivarAtual()">ARQUIVAR OCORRENCIA</button>
      <button class="b-prev" onclick="ocorrenciaAnterior()">OCORRENCIA ANTERIOR</button>
      <button class="b-bo" onclick="gerarRelatorioBO()">GERAR RELATORIO BO / PDF</button>
    </div>
  </div>
  <div class="card">
    <h3>RELATORIO SITUACIONAL (DECISAO ALFA)</h3>
    <div id="relatorio" class="box"></div>
  </div>
  <div class="card">
    <h3>DESARQUIVAR OCORRENCIA POR PERIODO</h3>
    <div class="botoes">
      <input type="datetime-local" id="dtIni">
      <input type="datetime-local" id="dtFim">
      <button class="b-desarq" onclick="desarquivar()">DESARQUIVAR PERIODO</button>
      <span id="msgDesarq" style="font-size:12px;color:#6ee7b7"></span>
    </div>
  </div>
</div>
<script>
function esc(s){
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function sincronizarLinkApresentacao(a){
  if(!a || !a.id) return;
  const tag = a.id + "-" + (a.codigo_sessao || "LIVE");
  const params = new URLSearchParams(window.location.search);
  if(params.get("oc") !== tag){
    params.set("oc", tag);
    const novaUrl = window.location.pathname + "?" + params.toString();
    window.history.replaceState(null, "", novaUrl);
  }
}
function obterEtapaRetorno(a){
  if(a.solucao_pronta) return 4;
  const et = Number(a.etapa_retorno || 0);
  if(et >= 1 && et <= 4) return et;
  if(a.acompanhando) return 3;
  const txt = String(a.retorno_bravo || "").toLowerCase();
  if(txt.includes("solucionad") || txt.includes("concluid") || txt.includes("encerrou o atendimento")) return 4;
  if(txt.includes("no local") || txt.includes("averiguacao") || txt.includes("averiguacao inicial")) return 2;
  if(txt.includes("deslocamento") || a.unidade_bravo) return 1;
  return 2;
}
async function enviarBravo(){
  const id = window._atualId || "";
  const unidade = document.getElementById("selUnidade").value;
  await fetch("/api/enviar-bravo",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,unidade})});
  atualizar();
}
async function acompanharOcorrencia(){
  const id = window._atualId || "";
  const unidade = document.getElementById("selUnidade").value;
  await fetch("/api/acompanhar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,unidade})});
  atualizar();
}
async function arquivarAtual(){
  const id = window._atualId || "";
  await fetch("/api/arquivar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});
  await atualizar();
}
async function ocorrenciaAnterior(){
  await fetch("/api/ocorrencia-anterior",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({})});
  await atualizar();
}
function gerarRelatorioBO(){
  const id = window._atualId || "";
  window.open("/api/relatorio-bo?oc_id=" + encodeURIComponent(id), "_blank");
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
async function atualizar(){
  const r = await fetch("/api/estado");
  const d = await r.json();
  document.getElementById("relogio").textContent = d.agora;
  document.getElementById("ciclo").textContent = fmtSeg(d.restante) + (d.pausado ? " [PAUSADO]" : "");
  document.getElementById("dbCount").textContent = d.total_persistido ?? 0;
  const a = d.atual;
  const badgeEnv = document.getElementById("statusEnvio");
  const endBox = document.getElementById("enderecoBox");
  const acompBox = document.getElementById("acompBox");
  const retBox = document.getElementById("retornoDestaque");
  const solBox = document.getElementById("solucaoBox");
  const btnAcomp = document.getElementById("btnAcompanhar");
  if(a){
    window._atualId = a.id;
    sincronizarLinkApresentacao(a);
    const acompAtivo = Boolean(a.acompanhando);
    btnAcomp.textContent = acompAtivo ? "ACOMPANHANDO OCORRENCIA" : "ACOMPANHAR OCORRENCIA";
    btnAcomp.className = acompAtivo ? "b-acomp-ativo" : "b-acomp";
    if(acompAtivo){
      acompBox.style.display = "block";
      acompBox.textContent = a.solucao_pronta
        ? "ACOMPANHAMENTO CONCLUIDO: Retorno de solucao recebido; clique em ARQUIVAR OCORRENCIA."
        : "ACOMPANHAMENTO ATIVO: Monitorando ocorrencia ate o retorno de solucao para o ALFA.";
    } else {
      acompBox.style.display = "none";
      acompBox.textContent = "";
    }
    document.getElementById("foto").src = "/imagem?t=" + encodeURIComponent(a.id + "_" + a.situacao_fluxo + "_" + (a.modo_analise || ""));
    document.getElementById("meta").textContent = "ID: "+a.id+" | "+a.arquivo+" ("+a.indice+"/"+a.total+") | "+a.situacao_fluxo;
    if(a.endereco){
      endBox.style.display = "block";
      endBox.innerHTML = "<b>ENDERECO DA OCORRENCIA:</b> " + esc(a.endereco);
    } else {
      endBox.style.display = "none";
      endBox.innerHTML = "";
    }
    if(a.unidade_bravo){
      badgeEnv.style.display = "block";
      badgeEnv.textContent = "DESPACHO CONFIRMADO: " + a.unidade_bravo;
    } else {
      badgeEnv.style.display = "none";
      badgeEnv.textContent = "";
    }
    if(a.retorno_bravo){
      const etapa = obterEtapaRetorno(a);
      const rotuloEtapa =
        etapa === 1 ? "ETAPA 1/4: DESLOCAMENTO" :
        etapa === 2 ? "ETAPA 2/4: AVERIGUACAO" :
        etapa === 3 ? "ETAPA 3/4: ACOMPANHAMENTO" :
        "ETAPA 4/4: SOLUÇÃO PRONTA";
      retBox.className = "retorno-destaque ret-e" + etapa;
      retBox.style.display = "block";
      retBox.innerHTML =
        "<span class='selo-etapa selo-e" + etapa + "'>" + esc(rotuloEtapa) + "</span> " +
        "RETORNO DO BRAVO: " + esc(a.retorno_bravo);
    } else {
      retBox.style.display = "none";
      retBox.innerHTML = "";
    }
    if(a.solucao_pronta && a.aviso_solucao){
      solBox.style.display = "block";
      solBox.innerHTML =
        "<span class='selo-etapa selo-e4'>ETAPA 4/4: SOLUÇÃO PRONTA</span> " +
        "AVISO AO ALFA: " + esc(a.aviso_solucao);
    } else {
      solBox.style.display = "none";
      solBox.textContent = "";
    }
    const objs = (a.objetos||[]).map(x => {
      const rot = esc(x.rotulo || "");
      const det = x.detalhe ? " (" + esc(x.detalhe) + ")" : "";
      return (rot + det).trim();
    }).filter(Boolean).join("; ") || "Nenhum item relevante";
    const pontos = (a.pontos_atencao||[]).map(p => esc(p)).join(" | ") || "Sem pontos adicionais";
    const cls = (a.nivel==="ALTO_RISCO"||a.nivel==="ATENCAO_ELEVADA")?"crit":(a.nivel==="OBSERVACAO"||a.nivel==="REGISTRO_OBJETO"?"med":"seg");
    document.getElementById("relatorio").innerHTML =
      "<div class='"+cls+"'><b>NIVEL:</b> "+esc(a.nivel)+" | <b>STATUS:</b> "+esc(a.status)+" | <b>PESSOAS:</b> "+esc(a.pessoas_visiveis ?? 0)+"</div>"+
      (a.endereco ? "<div class='linha-rel'><b>ENDERECO:</b> "+esc(a.endereco)+"</div>" : "")+
      "<div class='linha-rel'><b>SINTESE OPERACIONAL (ALFA):</b> "+esc(a.sintese_operacional || a.relato_visual)+"</div>"+
      "<div class='linha-rel'><b>ELEMENTOS NA CENA:</b> "+objs+"</div>"+
      "<div class='linha-rel'><b>RELATO DA OCORRENCIA:</b> "+esc(a.relato_visual)+"</div>"+
      "<div class='linha-rel'><b>PONTOS DE ATENCAO:</b> "+pontos+"</div>"+
      "<div class='linha-rel'><b>CONDICAO DO LOCAL:</b> "+esc(a.detalhes_cena)+"</div>";
  } else {
    btnAcomp.textContent = "ACOMPANHAR OCORRENCIA";
    btnAcomp.className = "b-acomp";
    acompBox.style.display = "none";
    endBox.style.display = "none";
    badgeEnv.style.display = "none";
    retBox.style.display = "none";
    solBox.style.display = "none";
    document.getElementById("meta").textContent = "Aguardando proxima ocorrencia...";
    document.getElementById("relatorio").innerHTML = "Nenhuma ocorrencia ativa no momento.";
  }
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
