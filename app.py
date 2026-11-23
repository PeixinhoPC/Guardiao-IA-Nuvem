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
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from PIL import Image, ImageFilter, ImageStat

TZ_BR = ZoneInfo("America/Sao_Paulo")
BASE_DIR = Path(__file__).resolve().parent
MONITOR_DIR = BASE_DIR / "IMAGENS_MONITOR"
INTERVALO_SEG = 120
MODO_DEMO_RETORNO_BRAVO = True
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL_ENV = os.environ.get("GEMINI_MODEL", "").strip()
MODELOS_CANDIDATOS = [m for m in [GEMINI_MODEL_ENV, "gemini-2.5-flash", "gemini-2.0-flash"] if m]

app = FastAPI(title="GUARDIAO DF-360 — SSPDF")


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
            if img_w < 300 or img_h < 300:
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
            otimizada.thumbnail((1024, 1024))
            buf = BytesIO()
            otimizada.save(buf, format="JPEG", quality=85, optimize=True)
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


def analise_visual_fallback(info: dict):
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
            "A inspeção visual identifica objetos ou superfícies com contornos nítidos no quadro, "
            "sem indicio conclusivo de armamento ou ameaça imediata isolada."
        )
    else:
        nivel = "NORMAL"
        status = "SEM_ANOMALIA_APARENTE"
        sintese = "Cena sem anomalias visuais evidentes no enquadramento atual."
        relato = (
            "A leitura visual da imagem indica cena sem elementos críticos isolados de forma conclusiva "
            "no momento da captura."
        )

    return {
        "status": status,
        "nivel": nivel,
        "pessoas_visiveis": 0,
        "objetos": objetos,
        "sintese_operacional": sintese,
        "relato_visual": relato,
        "pontos_atencao": pontos or ["Monitoramento continuo em curso"],
        "detalhes_cena": info["detalhes"]
    }


def extrair_json_robusto(texto: str):
    if not texto:
        return None
    limpo = re.sub(r"```(?:json)?|```", "", texto, flags=re.IGNORECASE).strip()
    for candidato in (limpo,):
        try:
            obj = json.loads(candidato)
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


def consultar_visao_externa(mime: str, img_b64: str):
    if not GEMINI_API_KEY or not img_b64:
        return None

    prompt = (
        "Voce e o modulo de analise visual do sistema GUARDIAO DF-360 (SSPDF). "
        "Analise EXCLUSIVAMENTE o que esta visivel na imagem (ignore completamente o nome do arquivo). "
        "Descreva com fidelidade o ambiente, objetos visiveis (incluindo carteira, documentos, celulares, "
        "veiculos, moveis, ferramentas, etc.), pessoas visiveis, posturas/acoes e eventual presenca de "
        "arma de fogo ou arma branca. "
        "Responda APENAS com um JSON valido (sem markdown, sem comentarios fora do JSON):\n"
        "{\n"
        '  "arma_fogo": bool,\n'
        '  "arma_branca": bool,\n'
        '  "pessoas_visiveis": int,\n'
        '  "nivel_sugerido": "NORMAL" | "OBSERVACAO" | "ATENCAO_ELEVADA" | "ALTO_RISCO",\n'
        '  "objetos": [{"rotulo": "string", "confianca": 0-100, "detalhe": "string"}],\n'
        '  "sintese_operacional": "string curta e objetiva para decisao do ALFA",\n'
        '  "relato_detalhado": "string completa e fiel do que esta na imagem",\n'
        '  "pontos_atencao": ["string"]\n'
        "}\n"
        "Nao mencione nomes de modelos, provedores de nuvem ou chaves de API. Seja direto e factual."
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

    for modelo in MODELOS_CANDIDATOS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        for tentativa in range(2):
            try:
                resp = requests.post(url, json=payload, timeout=45)
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.0 * (tentativa + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                candidatos = data.get("candidates") or []
                if not candidatos:
                    break
                partes = (candidatos[0].get("content") or {}).get("parts") or []
                texto_resp = "".join(str(p.get("text", "")) for p in partes if isinstance(p, dict)).strip()
                parsed = extrair_json_robusto(texto_resp)
                if parsed is not None:
                    return parsed
                if texto_resp:
                    return {
                        "arma_fogo": False,
                        "arma_branca": False,
                        "pessoas_visiveis": 0,
                        "nivel_sugerido": "OBSERVACAO",
                        "objetos": [],
                        "sintese_operacional": texto_resp[:220],
                        "relato_detalhado": texto_resp,
                        "pontos_atencao": []
                    }
            except Exception:
                time.sleep(0.6)
    return None


def sanitizar_texto_operacional(txt: str) -> str:
    if not txt:
        return ""
    limpo = re.sub(
        r"(?i)\b(gemini|google\s*ai\s*studio|generativelanguage|render|gemini_api_key|sem_chave_ia_configurada)\b",
        "analise visual",
        txt
    )
    return re.sub(r"\s{2,}", " ", limpo).strip()


def analisar_imagem_ia(caminho: Path):
    info = inspecionar_imagem(caminho)
    parsed = consultar_visao_externa(info["mime"], info["b64"])

    if not parsed:
        return analise_visual_fallback(info)

    arma_fogo = bool(parsed.get("arma_fogo", False))
    arma_branca = bool(parsed.get("arma_branca", False))
    try:
        pessoas = max(0, int(parsed.get("pessoas_visiveis", 0) or 0))
    except Exception:
        pessoas = 0

    objetos = []
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
                if rotulo:
                    objetos.append({"rotulo": rotulo, "confianca": conf_txt, "detalhe": det})

    relato = sanitizar_texto_operacional(
        str(parsed.get("relato_detalhado") or parsed.get("relato") or "Analise visual concluida.")
    )
    sintese = sanitizar_texto_operacional(str(parsed.get("sintese_operacional") or ""))
    if not sintese:
        sintese = relato[:220]

    pontos = []
    if isinstance(parsed.get("pontos_atencao"), list):
        for p in parsed.get("pontos_atencao"):
            sp = sanitizar_texto_operacional(str(p))
            if sp:
                pontos.append(sp)
    if not pontos:
        pontos = list(info.get("obs") or ["Cena avaliada"])

    nivel_ia = str(parsed.get("nivel_sugerido") or "").strip().upper()
    if arma_fogo:
        status = "ARMA_DE_FOGO_IDENTIFICADA"
        nivel = "ALTO_RISCO" if nivel_ia not in ("ATENCAO_ELEVADA", "ALTO_RISCO") else nivel_ia
    elif arma_branca:
        status = "ARMA_BRANCA_IDENTIFICADA"
        nivel = "ATENCAO_ELEVADA" if nivel_ia not in ("OBSERVACAO", "ATENCAO_ELEVADA", "ALTO_RISCO") else nivel_ia
    else:
        status = "ANALISE_VISUAL_CONCLUIDA"
        nivel = nivel_ia if nivel_ia in ("NORMAL", "OBSERVACAO", "ATENCAO_ELEVADA", "ALTO_RISCO") else "NORMAL"

    return {
        "status": status,
        "nivel": nivel,
        "pessoas_visiveis": pessoas,
        "objetos": objetos,
        "sintese_operacional": sintese,
        "relato_visual": relato,
        "pontos_atencao": pontos,
        "detalhes_cena": info["detalhes"]
    }


def avancar_ciclo():
    imagens = listar_imagens()
    if not imagens:
        return
    STATE["indice"] = (STATE["indice"] + 1) % len(imagens)
    arq = imagens[STATE["indice"]]
    agora = agora_br()
    analise = analisar_imagem_ia(arq)

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
        avancar_ciclo()
    while True:
        await asyncio.sleep(1)
        if not STATE["pausado"] and (time.time() - STATE["ultima_troca"]) >= INTERVALO_SEG:
            avancar_ciclo()


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(loop_autonomo())


@app.get("/imagem")
async def get_imagem():
    if STATE["atual"] and Path(STATE["atual"]["caminho"]).exists():
        return FileResponse(STATE["atual"]["caminho"])
    return JSONResponse({"erro": "Sem imagem"}, status_code=404)


@app.get("/api/estado")
async def api_estado():
    restante = max(0, int(INTERVALO_SEG - (time.time() - STATE["ultima_troca"]))) if not STATE["pausado"] else int(INTERVALO_SEG)
    ativas = [o for o in STATE["historico"] if o.get("situacao_fluxo") != "ARQUIVADA"]
    arquivadas = [o for o in STATE["historico"] if o.get("situacao_fluxo") == "ARQUIVADA"]
    agora = agora_br()
    return {
        "agora": agora.strftime("%d/%m/%Y — %H:%M:%S"),
        "restante": restante,
        "pausado": STATE["pausado"],
        "modo_demo_retorno": MODO_DEMO_RETORNO_BRAVO,
        "atual": STATE["atual"],
        "ativas": ativas[-40:],
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
    ativas_restantes = [o for o in STATE["historico"] if o.get("situacao_fluxo") != "ARQUIVADA"]
    if STATE["atual"] and STATE["atual"]["id"] == oc_id:
        STATE["atual"] = ativas_restantes[-1] if ativas_restantes else None
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
.box{background:#0b101b;border:1px solid #1e293b;border-radius:6px;padding:8px;max-height:195px;overflow-y:auto;overflow-x:hidden;font-size:12px;overflow-wrap:anywhere;word-break:break-word;max-width:100%}
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
      <div id="relatorio" class="box" style="max-height:240px"></div>
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
      <div id="boxMsg" class="box"></div>
    </div>
    <div class="card">
      <h3>OCORRENCIAS ATIVAS / HISTORICO (ROLAGEM DE BAIXO PARA CIMA)</h3>
      <div id="boxHist" class="box"></div>
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
    document.getElementById("foto").src = "/imagem?t=" + encodeURIComponent(a.id + "_" + a.situacao_fluxo);
    document.getElementById("meta").textContent = "ID: "+a.id+" | "+a.arquivo+" ("+a.indice+"/"+a.total+") | "+a.situacao_fluxo;
    const objs = (a.objetos||[]).map(x => {
      const rot = esc(x.rotulo || "");
      const det = x.detalhe ? " [" + esc(x.detalhe) + "]" : "";
      const conf = x.confianca ? " (" + esc(x.confianca) + ")" : "";
      return (rot + det + conf).trim();
    }).filter(Boolean).join("; ") || "Nenhum objeto critico isolado";
    const pontos = (a.pontos_atencao||[]).map(p => esc(p)).join(" | ") || "Sem pontos adicionais";
    const cls = (a.nivel==="ALTO_RISCO"||a.nivel==="ATENCAO_ELEVADA")?"crit":(a.nivel==="OBSERVACAO"||a.nivel==="REGISTRO_OBJETO"?"med":"seg");
    document.getElementById("relatorio").innerHTML =
      "<div class='"+cls+"'><b>NIVEL:</b> "+esc(a.nivel)+" | <b>STATUS:</b> "+esc(a.status)+" | <b>PESSOAS:</b> "+esc(a.pessoas_visiveis ?? 0)+"</div>"+
      "<div class='linha-rel'><b>SINTESE OPERACIONAL (ALFA):</b> "+esc(a.sintese_operacional || a.relato_visual)+"</div>"+
      "<div class='linha-rel'><b>ELEMENTOS / OBJETOS VISIVEIS:</b> "+objs+"</div>"+
      "<div class='linha-rel'><b>RELATO COMPLETO DA CENA:</b> "+esc(a.relato_visual)+"</div>"+
      "<div class='linha-rel'><b>PONTOS DE ATENCAO:</b> "+pontos+"</div>"+
      "<div class='linha-rel'><b>AMBIENTE / TELEMETRIA:</b> "+esc(a.detalhes_cena)+"</div>"+
      (a.retorno_bravo ? "<div class='linha-rel'><b>RETORNO BRAVO:</b> "+esc(a.retorno_bravo)+"</div>" : "");
  }
  const boxBravo = document.getElementById("boxBravo");
  const boxMsg = document.getElementById("boxMsg");
  const boxHist = document.getElementById("boxHist");
  const listaArq = document.getElementById("listaArq");
  boxBravo.innerHTML = (d.retorno_bravo||[]).map(x => "<div class='item'>["+esc(x.hora)+"] <b>"+esc(x.ocorrencia_id)+":</b> "+esc(x.texto)+"</div>").join("");
  boxMsg.innerHTML = (d.mensagens||[]).map(x => "<div class='item'>["+esc(x.hora)+"] <b>"+esc(x.origem)+":</b> "+esc(x.texto)+"</div>").join("");
  boxHist.innerHTML = (d.ativas||[]).map(x => "<div class='item'><b>"+esc(x.id)+"</b> ("+esc(x.data_hora)+") ["+esc(x.situacao_fluxo)+"] — <b>"+esc(x.nivel)+"</b>: "+esc(x.sintese_operacional || x.relato_visual)+"</div>").join("");
  listaArq.innerHTML = (d.arquivadas||[]).map(x => "<div class='item'>[ARQUIVADA] <b>"+esc(x.id)+"</b> — "+esc(x.data_hora)+" — "+esc(x.arquivo)+"</div>").join("");
  rolarBaixo(boxBravo);
  rolarBaixo(boxMsg);
  rolarBaixo(boxHist);
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
