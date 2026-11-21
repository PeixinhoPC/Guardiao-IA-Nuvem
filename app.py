# -*- coding: utf-8 -*-
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from ultralytics import YOLO
import uvicorn
import os
import glob
import shutil
from PIL import Image
from datetime import datetime
import time

app = FastAPI(title="GUARDIÃO DF-360 — VIGILÂNCIA PÚBLICA")

# ================= CONFIGURAÇÕES =================
PASTA_MONITORADA = "IMAGENS_MONITOR/IMAGENS_MONITOR"
TEMPO_EXIBICAO = 120  # 2 minutos em segundos
os.makedirs(PASTA_MONITORADA, exist_ok=True)

# Estado global
indice_atual = 0
ultima_atualizacao = time.time()
ocorrencia_atual = None
historico_alertas = []
comunicacoes = []
lista_unidades_bravo = [
    "1ª Companhia — Centro",
    "2ª Companhia — Ceilândia",
    "3ª Companhia — Taguatinga",
    "4ª Companhia — Planaltina",
    "5ª Companhia — Samambaia",
    "6ª Companhia — Recanto das Emas",
    "Batalhão de Operações Especiais",
    "Centro de Comando e Controle"
]

# Carregar modelo
modelo = None
nomes_das_classes = {}
try:
    modelo = YOLO("best.pt")
    nomes_das_classes = modelo.names
    print("✅ Modelo IA carregado")
except Exception as e:
    print(f"⚠️ Modelo não carregado: {e}")

# ================= FUNÇÕES =================
def listar_imagens():
    imagens = []
    for ext in ["*.jpg", "*.jpeg", "*.png", "*.JPG", "*.JPEG"]:
        imagens.extend(glob.glob(os.path.join(PASTA_MONITORADA, "**", ext), recursive=True))
    return sorted(imagens)

def analisar_imagem(caminho_imagem):
    if modelo is None:
        return None, "Modelo não disponível"
    try:
        img = Image.open(caminho_imagem)
        resultados = modelo(img, verbose=False)[0]
        deteccoes = []
        for obj in resultados.boxes:
            classe_id = int(obj.cls[0])
            classe = nomes_das_classes.get(classe_id, f"desconhecido")
            confianca = round(float(obj.conf[0]) * 100, 1)
            deteccoes.append({"classe": classe, "confianca": confianca})
        
        armas_fogo = [d for d in deteccoes if any(p in d["classe"].lower() 
                     for p in ["gun", "pistol", "rifle", "firearm", "weapon"])]
        armas_brancas = [d for d in deteccoes if any(p in d["classe"].lower() 
                       for p in ["knife", "blade", "dagger"])]
        pessoas = [d for d in deteccoes if "person" in d["classe"].lower()]
        
        if armas_fogo:
            tipo = "🔴 ARMA DE FOGO DETECTADA"
            nivel = "vermelho"
            conf = max(d["confianca"] for d in armas_fogo)
        elif armas_brancas:
            tipo = "🟠 ARMA BRANCA DETECTADA"
            nivel = "laranja"
            conf = max(d["confianca"] for d in armas_brancas)
        elif pessoas:
            tipo = "🟡 PESSOA DETECTADA"
            nivel = "amarelo"
            conf = pessoas[0]["confianca"]
        elif deteccoes:
            tipo = "⚠️ OBJETO DETECTADO"
            nivel = "normal"
            conf = deteccoes[0]["confianca"]
        else:
            tipo = "✅ SEM DETECÇÕES DE RISCO"
            nivel = "seguro"
            conf = 0
        
        agora = datetime.now()
        id_alerta = agora.strftime("%Y%m%d_%H%M%S")
        
        return {
            "id": id_alerta,
            "imagem": os.path.basename(caminho_imagem),
            "tipo": tipo,
            "nivel": nivel,
            "confianca": conf,
            "deteccoes": deteccoes,
            "detalhe": ", ".join([f"{d['classe']} ({d['confianca']}%)" for d in deteccoes]) or "Nenhuma detecção",
            "hora": agora.strftime("%d/%m/%Y — %H:%M:%S"),
            "caminho": caminho_imagem
        }, None
    except Exception as e:
        return None, str(e)

def avancar_se_tempo_passou():
    global indice_atual, ultima_atualizacao, ocorrencia_atual, historico_alertas
    agora = time.time()
    if agora - ultima_atualizacao >= TEMPO_EXIBICAO:
        imagens = listar_imagens()
        if imagens:
            indice_atual = (indice_atual + 1) % len(imagens)
            resultado, erro = analisar_imagem(imagens[indice_atual])
            if resultado:
                ocorrencia_atual = resultado
                historico_alertas.append(resultado)
        ultima_atualizacao = agora

# ================= ROTAS =================
@app.get("/", response_class=HTMLResponse)
async def pagina_principal():
    avancar_se_tempo_passou()
    return """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <title>🛡️ GUARDIÃO DF-360 — VIGILÂNCIA PÚBLICA</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Arial, sans-serif; }
        body { background: #08080c; color: #e5e5e5; min-height: 100vh; }
        
        .cabecalho { background: linear-gradient(90deg, #001a33, #002b55); padding: 12px 25px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #0066aa; }
        .cabecalho h1 { font-size: 22px; color: #ffffff; }
        .relogio { font-size: 18px; font-weight: bold; color: #66ddff; font-family: Consolas, monospace; }
        .status-indicador { display: flex; align-items: center; gap: 8px; }
        .bolinha { width: 12px; height: 12px; background: #00cc44; border-radius: 50%; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }
        
        .container { max-width: 1200px; margin: 15px auto; padding: 0 15px; }
        .grade { display: grid; grid-template-columns: 1fr 320px; gap: 15px; }
        .card { background: #101018; border-radius: 8px; border: 1px solid #203040; overflow: hidden; }
        .card-titulo { background: #181828; padding: 10px 15px; font-size: 14px; font-weight: bold; color: #88ccff; border-bottom: 1px solid #253545; }
        .card-corpo { padding: 15px; }
        
        .tela-imagem { background: #050508; width: 100%; height: 380px; display: flex; align-items: center; justify-content: center; border-radius: 5px; margin-bottom: 10px; overflow: hidden; }
        .tela-imagem span { color: #555; font-style: italic; }
        .tela-imagem img { max-width: 100%; max-height: 100%; object-fit: contain; cursor: zoom-in; transition: transform 0.3s; }
        .tela-imagem img.zoom { transform: scale(1.5); cursor: zoom-out; }
        
        .alerta-vermelho { background: #220000; border-left: 4px solid #cc0000; }
        .alerta-laranja { background: #221600; border-left: 4px solid #cc6600; }
        .alerta-amarelo { background: #1f1f00; border-left: 4px solid #aaaa00; }
        .alerta-normal { background: #121820; border-left: 4px solid #335577; }
        .alerta-seguro { background: #0f1f0f; border-left: 4px solid #226622; }
        .caixa-alerta { padding: 12px; border-radius: 4px; margin-bottom: 10px; }
        .caixa-alerta strong { font-size: 16px; display: block; margin-bottom: 8px; }
        .caixa-alerta div { font-size: 14px; margin: 4px 0; }
        
        .btn { padding: 10px 12px; border: none; border-radius: 5px; font-size: 13px; font-weight: bold; cursor: pointer; width: 100%; margin-bottom: 8px; transition: opacity 0.2s; }
        .btn:hover { opacity: 0.9; }
        .btn-bravo { background: #770000; color: white; }
        .btn-arquivar { background: #1a4466; color: white; }
        .btn-proximo { background: #225533; color: white; }
        .btn-upload { background: #2266aa; color: white; }
        .btn-comunicado { background: #443366; color: white; }
        
        .caixa-comunicado { height: 100px; background: #0a0a12; border: 1px solid #252545; border-radius: 5px; padding: 8px; margin-bottom: 8px; overflow-y: auto; font-size: 12px; }
        .msg-alfa { color: #aaddff; margin-bottom: 4px; }
        .msg-guardiao { color: #ffddaa; margin-bottom: 4px; }
        .entrada-texto { width: 100%; padding: 8px; background: #0a0a12; border: 1px solid #333355; color: white; border-radius: 4px; margin-bottom: 5px; }
        
        .linha-relatorio { padding: 6px 8px; border-bottom: 1px solid #1a1a28; font-size: 12px; }
        .sem-dados { text-align: center; padding: 25px; color: #555; font-style: italic; }
        
        .modal-fundo { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 100; align-items: center; justify-content: center; }
        .modal-aberto { display: flex; }
        .modal-conteudo { background: #151525; border: 2px solid #335577; border-radius: 8px; padding: 20px; max-width: 450px; width: 90%; }
        .modal-titulo { font-size: 16px; font-weight: bold; margin-bottom: 15px; color: #88ccff; }
        .opcao-unidade { padding: 10px 12px; background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 4px; margin-bottom: 6px; cursor: pointer; }
        .opcao-unidade:hover { background: #252545; }
        .btn-fechar { background: #444; color: white; border: none; padding: 6px 15px; border-radius: 4px; margin-top: 12px; cursor: pointer; }
    </style>
</head>
<body onload="atualizarRelogio(); carregarDados();">
    <div class="cabecalho">
        <h1>🛡️ GUARDIÃO DF-360 — SISTEMA DE VIGILÂNCIA PÚBLICA</h1>
        <div class="status-indicador">
            <span class="bolinha"></span>
            <span>VIGILÂNCIA ATIVA</span>
        </div>
        <div class="relogio" id="relogio">00:00:00</div>
    </div>

    <div class="container">
        <div class="grade">
            <div>
                <div class="card">
                    <div class="card-titulo">📷 IMAGEM EM ANÁLISE — Troca automática a cada 2 minutos</div>
                    <div class="card-corpo">
                        <div class="tela-imagem" id="telaImagem" onclick="toggleZoom()">
                            <span>Aguardando imagem...</span>
                        </div>
                        <div id="caixaAlerta" class="caixa-alerta alerta-seguro">
                            <strong>Aguardando análise...</strong>
                        </div>
                    </div>
                </div>

                <div class="card" style="margin-top:15px;">
                    <div class="card-titulo">📋 RELATÓRIO DE OCORRÊNCIAS</div>
                    <div class="card-corpo" id="relatorio">
                        <div class="sem-dados">Aguardando primeira análise...</div>
                    </div>
                </div>
            </div>

            <div>
                <div class="card">
                    <div class="card-titulo">⚡ AÇÕES DO ALFA</div>
                    <div class="card-corpo">
                        <button class="btn btn-bravo" onclick="abrirModalUnidades()">🚨 ENVIAR AO BRAVO</button>
                        <button class="btn btn-arquivar" onclick="arquivar()">📁 ARQUIVAR OCORRÊNCIA</button>
                        <button class="btn btn-proximo" onclick="forcarProximaImagem()">⏭️ PRÓXIMA IMAGEM</button>
                        <label class="btn btn-upload" style="display:block; text-align:center; cursor:pointer; margin-bottom:8px;">
                            📤 CARREGAR IMAGEM
                            <input type="file" accept="image/*" style="display:none;" onchange="enviarImagem(this)">
                        </label>
                    </div>
                </div>

                <div class="card" style="margin-top:15px;">
                    <div class="card-titulo">💬 COMUNICAÇÃO — ALFA ↔ GUARDIÃO</div>
                    <div class="card-corpo">
                        <div class="caixa-comunicado" id="caixaComunicacao">
                            <div class="msg-guardiao">GUARDIÃO: Sistema online. Aguardando solicitações.</div>
                        </div>
                        <input type="text" class="entrada-texto" id="entradaMensagem" placeholder="Digite sua solicitação ao Guardião...">
                        <button class="btn btn-comunicado" onclick="enviarComunicado()">📤 ENVIAR SOLICITAÇÃO</button>
                    </div>
                </div>

                <div class="card" style="margin-top:15px;">
                    <div class="card-titulo">📊 RESUMO DO DIA</div>
                    <div class="card-corpo">
                        <div style="margin-bottom:8px;">📷 Imagens processadas: <strong id="qtdImagens">0</strong></div>
                        <div style="margin-bottom:8px;">🔴 Alertas críticos: <strong id="qtdVermelho">0</strong></div>
                        <div style="margin-bottom:8px;">🟠 Alertas médios: <strong id="qtdLaranja">0</strong></div>
                        <div>✅ Sem risco: <strong id="qtdSeguro">0</strong></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div id="modalUnidades" class="modal-fundo" onclick="fecharModalFora(event)">
        <div class="modal-conteudo" onclick="event.stopPropagation()">
            <div class="modal-titulo">🚨 SELECIONE A UNIDADE DESTINO</div>
            <div id="listaUnidades"></div>
            <button class="btn-fechar" onclick="fecharModalUnidades()">Cancelar</button>
        </div>
    </div>

    <script>
        let estadoZoom = false;
        
        function atualizarRelogio() {
            const agora = new Date();
            document.getElementById('relogio').textContent = agora.toLocaleTimeString('pt-BR');
            setTimeout(atualizarRelogio, 1000);
        }
        
        function toggleZoom() {
            const img = document.querySelector('#telaImagem img');
            if (img) {
                estadoZoom = !estadoZoom;
                img.classList.toggle('zoom', estadoZoom);
            }
        }
        
        function abrirModalUnidades() {
            const unidades = [
                "1ª Companhia — Centro",
                "2ª Companhia — Ceilândia",
                "3ª Companhia — Taguatinga",
                "4ª Companhia — Planaltina",
                "5ª Companhia — Samambaia",
                "6ª Companhia — Recanto das Emas",
                "Batalhão de Operações Especiais",
                "Centro de Comando e Controle"
            ];
            document.getElementById('listaUnidades').innerHTML = 
                unidades.map(u => `<div class="opcao-unidade" onclick="enviarParaBravo('${u}')">${u}</div>`).join('');
            document.getElementById('modalUnidades').classList.add('modal-aberto');
        }
        
        function fecharModalUnidades
        function fecharModalUnidades() {
            document.getElementById('modalUnidades').classList.remove('modal-aberto');
        }
        
        function fecharModalFora(event) {
            if (event.target.id === 'modalUnidades') fecharModalUnidades();
        }
        
        function enviarParaBravo(unidade) {
            fecharModalUnidades();
            alert(`✅ Ocorrência enviada para: ${unidade}`);
        }
        
        function arquivar() {
            alert("✅ Ocorrência arquivada");
        }
        
        async function enviarImagem(input) {
            if (!input.files.length) return;
            const arquivo = input.files[0];
            const formData = new FormData();
            formData.append("arquivo", arquivo);
            const res = await fetch("/upload-imagem", {
                method: "POST",
                body: formData
            });
            const dados = await res.json();
            alert(dados.mensagem);
            carregarDados();
        }
        
        async function forcarProximaImagem() {
            const res = await fetch('/analisar-proxima', { method: 'POST' });
            const dados = await res.json();
            if (dados.status === 'sucesso') {
                atualizarTela(dados.ocorrencia);
            }
        }
        
        async function carregarDados() {
            const res = await fetch('/status');
            const dados = await res.json();
            document.getElementById('qtdImagens').textContent = dados.total_imagens;
            document.getElementById('qtdVermelho').textContent = dados.historico.filter(h => h.nivel === 'vermelho').length;
            document.getElementById('qtdLaranja').textContent = dados.historico.filter(h => h.nivel === 'laranja').length;
            document.getElementById('qtdSeguro').textContent = dados.historico.filter(h => h.nivel === 'seguro').length;
            if (dados.atual) atualizarTela(dados.atual);
            atualizarRelatorio(dados.historico);
        }
        
        function atualizarTela(ocorrencia) {
            const tela = document.getElementById('telaImagem');
            const alerta = document.getElementById('caixaAlerta');
            
            if (!ocorrencia) {
                tela.innerHTML = '<span>Aguardando imagem...</span>';
                alerta.className = 'caixa-alerta alerta-seguro';
                alerta.innerHTML = '<strong>Aguardando análise...</strong>';
                return;
            }
            
            tela.innerHTML = `<img src="/imagens/${encodeURIComponent(ocorrencia.imagem)}" alt="Imagem">`;
            alerta.className = `caixa-alerta alerta-${ocorrencia.nivel}`;
            alerta.innerHTML = `
                <strong>${ocorrencia.tipo}</strong>
                <div>📄 Arquivo: ${ocorrencia.imagem}</div>
                <div>⏰ Hora: ${ocorrencia.hora}</div>
                <div>🔍 Detecção: ${ocorrencia.detalhe}</div>
            `;
        }
        
        function atualizarRelatorio(historico) {
            const el = document.getElementById('relatorio');
            if (!historico.length) {
                el.innerHTML = '<div class="sem-dados">Aguardando primeira análise...</div>';
                return;
            }
            el.innerHTML = historico.map(h => `
                <div class="linha-relatorio">
                    <strong>${h.tipo}</strong> — ${h.imagem} — ${h.hora}
                </div>
            `).join('');
        }
        
        async function enviarComunicado() {
            const input = document.getElementById('entradaMensagem');
            const texto = input.value.trim();
            if (!texto) return;
            
            const caixa = document.getElementById('caixaComunicacao');
            caixa.innerHTML += `<div class="msg-alfa">ALFA: ${texto}</div>`;
            input.value = '';
            
            const res = await fetch('/alfa-comunica', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `mensagem=${encodeURIComponent(texto)}`
            });
            const dados = await res.json();
            caixa.innerHTML += `<div class="msg-guardiao">GUARDIÃO: ${dados.resposta}</div>`;
            caixa.scrollTop = caixa.scrollHeight;
        }
        
        setInterval(carregarDados, 30000);
    </script>
</body>
</html>
"""

# ================= ROTAS ADICIONAIS =================
@app.get("/status")
async def get_status():
    avancar_se_tempo_passou()
    return {
        "total_imagens": len(listar_imagens()),
        "total_alertas": len(historico_alertas),
        "ultima_atualizacao": datetime.now().strftime("%H:%M:%S"),
        "atual": ocorrencia_atual,
        "historico": list(reversed(historico_alertas[-15:]))
    }

@app.post("/analisar-proxima")
async def analisar_proxima():
    global indice_atual, ultima_atualizacao, ocorrencia_atual, historico_alertas
    imagens = listar_imagens()
    if not imagens:
        return {"status": "sem_imagens", "mensagem": "Nenhuma imagem na pasta IMAGENS_MONITOR"}
    
    indice_atual = (indice_atual + 1) % len(imagens)
    resultado, erro = analisar_imagem(imagens[indice_atual])
    if erro:
        return {"status": "erro", "mensagem": erro}
    
    ocorrencia_atual = resultado
    historico_alertas.append(resultado)
    ultima_atualizacao = time.time()
    return {"status": "sucesso", "ocorrencia": resultado}

@app.post("/alfa-comunica")
async def alfa_comunica(mensagem: str = Form(...)):
    comunicacoes.append({"quem": "ALFA", "texto": mensagem, "hora": datetime.now().strftime("%H:%M:%S")})
    resposta = "Recebido. Processando solicitação."
    comunicacoes.append({"quem": "GUARDIÃO", "texto": resposta, "hora": datetime.now().strftime("%H:%M:%S")})
    return {"resposta": resposta}

@app.get("/comunicacoes")
async def get_comunicacoes():
    return {"mensagens": comunicacoes[-10:]}

@app.get("/imagens/{nome_arquivo}")
async def servir_imagem(nome_arquivo: str):
    caminho = os.path.join(PASTA_MONITORADA, nome_arquivo)
    if os.path.exists(caminho):
        return FileResponse(caminho)
    return {"erro": "Arquivo não encontrado"}

@app.post("/upload-imagem")
async def upload_imagem(arquivo: UploadFile = File(...)):
    os.makedirs(PASTA_MONITORADA, exist_ok=True)
    caminho = os.path.join(PASTA_MONITORADA, arquivo.filename)
    with open(caminho, "wb") as f:
        shutil.copyfileobj(arquivo.file, f)
    return {"status": "sucesso", "mensagem": f"Imagem {arquivo.filename} enviada!"}

if __name__ == "__main__":
    print("🚀 GUARDIÃO DF-360 INICIADO")
    print(f"📂 Pasta monitorada: {PASTA_MONITORADA}")
    print(f"📷 Imagens encontradas: {len(listar_imagens())}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
