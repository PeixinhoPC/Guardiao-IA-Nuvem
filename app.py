import asyncio
import time
import uuid
import random
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from ultralytics import YOLO
from PIL import Image, ImageStat

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "best.pt"
MONITOR_DIR = BASE_DIR / "IMAGENS_MONITOR"
INTERVALO_SEG = 120
MODO_DEMO_RETORNO_BRAVO = True

app = FastAPI(title="GUARDIAO DF-360 — SSPDF")

modelo_armas = None
erro_modelo = None
if MODEL_PATH.exists():
    try:
        modelo_armas = YOLO(str(MODEL_PATH))
    except Exception as exc:
        erro_modelo = str(exc)
else:
    erro_modelo = "Arquivo best.pt nao encontrado no repositorio."

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
            "hora": datetime.now().strftime("%H:%M:%S"),
            "texto": "GUARDIAO DF-360 online na nuvem. Relato objetivo de cena a cada 2 minutos."
        }
    ],
    "retorno_bravo": [
        {
            "hora": datetime.now().strftime("%H:%M:%S"),
            "ocorrencia_id": "SISTEMA",
            "texto": "Aguardando despacho do ALFA para a equipe BRAVO."
        }
    ]
}

def classificar_arma(nome_cls: str, xyxy, conf: float):
    x1, y1, x2, y2 = xyxy
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    razao = bw / bh
    c = nome_cls.lower().strip()

    if c in ("pistol", "handgun", "pistola"):
        return "pistola", "Pistola"
    if c in ("firearm", "gun", "revolver", "rifle", "shotgun", "weapon"):
        return "firearm", ("Pistola / Arma de fogo" if razao >= 0.75 else "Arma de fogo")
    if c == "knife":
        if conf < 75.0 and 0.55 <= razao <= 2.4:
            return "firearm", "Pistola / Arma de fogo"
        return "knife", "Arma branca (faca)"
    return None, None

def analisar_imagem(caminho: Path):
    objetos = []
    armas = []
    obs_cena = []
    img_w, img_h = 1, 1

    try:
        with Image.open(caminho) as im:
            im_rgb = im.convert("RGB")
            img_w, img_h = im_rgb.size
            brilho = sum(ImageStat.Stat(im_rgb).mean) / 3.0
            if brilho < 55:
                obs_cena.append("Baixa luminosidade")
            if img_w < 300 or img_h < 300:
                obs_cena.append("Resolucao reduzida")
    except Exception as exc:
        obs_cena.append(f"Falha ao abrir imagem: {exc}")

    if modelo_armas is not None:
        try:
            resultados = modelo_armas.predict(source=str(caminho), conf=0.25, verbose=False)
            for r in resultados:
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    nome_cls = modelo_armas.names.get(cls_id, str(cls_id)).lower()
                    conf = round(float(box.conf[0]) * 100, 1)
                    xyxy = [float(x) for x in box.xyxy[0].tolist()]
                    tipo_arma, rotulo_arma = classificar_arma(nome_cls, xyxy, conf)
                    if tipo_arma:
                        item = {"classe": tipo_arma, "rotulo": rotulo_arma, "confianca": conf}
                        objetos.append(item)
                        armas.append(item)
                    else:
                        trad = {
                            "phone": "Celular/Objeto",
                            "wallet": "Carteira",
                            "person": "Pessoa"
                        }.get(nome_cls, nome_cls)
                        objetos.append({"classe": nome_cls, "rotulo": trad, "confianca": conf})
        except Exception as exc:
            obs_cena.append(f"Falha na inferencia: {exc}")
    else:
        obs_cena.append(f"Modelo indisponivel: {erro_modelo}")

    if armas:
        arma_txt = ", ".join(f"{a['rotulo']} ({a['confianca']}%)" for a in armas)
        status = "ARMA_IDENTIFICADA"
        nivel = "ATENCAO_ELEVADA"
        relato = f"Visualizado(s) {arma_txt} na imagem analisada."
    else:
        status = "SEM_ARMA_IDENTIFICADA"
        nivel = "NORMAL"
        relato = "Nenhuma arma identificada na imagem analisada."

    if not obs_cena:
        obs_cena.append("Enquadramento e iluminacao regulares.")

    return {
        "status": status,
        "nivel": nivel,
        "objetos": objetos,
        "relato_visual": relato,
        "detalhes_cena": f"{img_w}x{img_h}px | " + " | ".join(obs_cena)
    }

def avancar_ciclo():
    imagens = listar_imagens()
    if not imagens:
        return
    STATE["indice"] = (STATE["indice"] + 1) % len(imagens)
    arq = imagens[STATE["indice"]]
    agora = datetime.now()
    analise = analisar_imagem(arq)

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
        "texto": f"[{oc['id']}] {oc['nivel']} — {oc['relato_visual']}"
    })
    STATE["mensagens"] = STATE["mensagens"][-150:]

async def simular_retorno_bravo_demo(oc_id: str):
    if not MODO_DEMO_RETORNO_BRAVO:
        return
    await asyncio.sleep(8)
    hora = datetime.now().strftime("%H:%M:%S")
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
    return {
        "agora": datetime.now().strftime("%d/%m/%Y — %H:%M:%S"),
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
    hora = datetime.now().strftime("%H:%M:%S")
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
        hora = datetime.now().strftime("%H:%M:%S")
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
                odt = datetime.fromisoformat(o.get("iso_dt", ""))
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
        hora = datetime.now().strftime("%H:%M:%S")
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
    return """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GUARDIAO DF-360</title>
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
.box{background:#0b101b;border:1px solid #1e293b;border-radius:6px;padding:8px;max-height:165px;overflow-y:auto;overflow-x:hidden;font-size:12px;overflow-wrap:anywhere;word-break:break-word;max-width:100%}
.item{border-bottom:1px solid #1e293b;padding:5px 0}
.crit{color:#fca5a5}.med{color:#fcd34d}.seg{color:#86efac}
@media(max-width:980px){.grid{grid-template-columns:minmax(0,1fr)}}
</style></head><body>
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
      <h3>RELATORIO DE ANALISE DO QUE A IA VE</h3>
      <div id="relatorio" class="box" style="max-height:190px"></div>
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
    const objs = (a.objetos||[]).map(x => x.rotulo + " ("+x.confianca+"%)").join(", ") || "Nada classificado acima do limiar";
    const cls = (a.nivel==="ALTO_RISCO"||a.nivel==="ATENCAO_ELEVADA")?"crit":(a.nivel==="OBSERVACAO"||a.nivel==="REGISTRO_OBJETO"?"med":"seg");
    document.getElementById("relatorio").innerHTML =
      "<div class='"+cls+"'><b>NIVEL:</b> "+a.nivel+" | <b>STATUS:</b> "+a.status+"</div>"+
      "<div><b>OBJETOS IDENTIFICADOS:</b> "+objs+"</div>"+
      "<div><b>RELATO DA CENA:</b> "+a.relato_visual+"</div>"+
      "<div><b>DETALHES DO AMBIENTE:</b> "+a.detalhes_cena+"</div>"+
      (a.retorno_bravo ? "<div><b>RETORNO BRAVO:</b> "+a.retorno_bravo+"</div>" : "");
  }
  const boxBravo = document.getElementById("boxBravo");
  const boxMsg = document.getElementById("boxMsg");
  const boxHist = document.getElementById("boxHist");
  const listaArq = document.getElementById("listaArq");
  boxBravo.innerHTML = (d.retorno_bravo||[]).map(x => "<div class='item'>["+x.hora+"] <b>"+x.ocorrencia_id+":</b> "+x.texto+"</div>").join("");
  boxMsg.innerHTML = (d.mensagens||[]).map(x => "<div class='item'>["+x.hora+"] <b>"+x.origem+":</b> "+x.texto+"</div>").join("");
  boxHist.innerHTML = (d.ativas||[]).map(x => "<div class='item'><b>"+x.id+"</b> ("+x.data_hora+") ["+x.situacao_fluxo+"] — "+x.nivel+": "+x.relato_visual+"</div>").join("");
  listaArq.innerHTML = (d.arquivadas||[]).map(x => "<div class='item'>[ARQUIVADA] <b>"+x.id+"</b> — "+x.data_hora+" — "+x.arquivo+"</div>").join("");
  rolarBaixo(boxBravo);
  rolarBaixo(boxMsg);
  rolarBaixo(boxHist);
  rolarBaixo(listaArq);
}
atualizar();
setInterval(atualizar, 1000);
</script></body></html>"""

if __name__ == "__main__":
    import os
    import uvicorn
    porta = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=porta, workers=1)
