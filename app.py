# -*- coding: utf-8 -*-
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from ultralytics import YOLO
import uvicorn
import os
from PIL import Image
import io

app = FastAPI(title="GUARDIÃO DF-360 — Detecção de Armas")

# Carregar modelo
modelo = None
nomes_das_classes = {}

try:
    modelo = YOLO("best.pt")
    nomes_das_classes = modelo.names
    print("✅ Modelo IA carregado com sucesso!")
except Exception as e:
    print(f"⚠️ Erro ao carregar modelo: {e}")

# Página principal
@app.get("/", response_class=HTMLResponse)
async def pagina_principal():
    return """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🛡️ GUARDIÃO DF-360 — Vigilância Pública</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', sans-serif; }
        body { background: #0a0a0f; color: #e0e0e0; min-height: 100vh; }
        .cabecalho { background: linear-gradient(90deg, #002244, #003366); padding: 20px; text-align: center; border-bottom: 3px solid #0066aa; }
        .cabecalho h1 { font-size: 28px; color: #ffffff; }
        .cabecalho p { color: #88ccff; margin-top: 5px; }
        .container { max-width: 900px; margin: 30px auto; padding: 0 20px; }
        .card { background: #151520; border-radius: 10px; padding: 25px; margin-bottom: 25px; border: 1px solid #2a2a3a; }
        .card h2 { font-size: 18px; margin-bottom: 15px; color: #44aaff; border-bottom: 1px solid #2a2a3a; padding-bottom: 8px; }
        .upload-area { border: 2px dashed #4466aa; border-radius: 8px; padding: 40px; text-align: center; cursor: pointer; transition: all 0.3s; }
        .upload-area:hover { border-color: #66aaff; background: #1a1a2a; }
        input[type="file"] { display: none; }
        .btn { background: #0066aa; color: white; border: none; padding: 10px 25px; border-radius: 5px; font-size: 15px; cursor: pointer; margin-top: 15px; }
        .btn:hover { background: #0088dd; }
        .resultado { margin-top: 20px; padding: 15px; border-radius: 6px; display: none; }
        .resultado.sucesso { background: #0a2a1a; border: 1px solid #228844; display: block; }
        .resultado.alerta-vermelho { background: #330000; border: 1px solid #cc0000; display: block; }
        .resultado.alerta-laranja { background: #332200; border: 1px solid #cc8800; display: block; }
        .resultado.alerta-amarelo { background: #2e2e00; border: 1px solid #aaaa00; display: block; }
        .img-preview { max-width: 100%; margin-top: 15px; border-radius: 6px; display: none; }
        .detalhes { margin-top: 10px; font-size: 14px; line-height: 1.8; }
        .status-sistema { background: #112211; border: 1px solid #228844; padding: 10px 15px; border-radius: 5px; margin-bottom: 20px; }
        .status-indicador { display: inline-block; width: 12px; height: 12px; background: #00cc44; border-radius: 50%; margin-right: 8px; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="cabecalho">
        <h1>🛡️ GUARDIÃO DF-360</h1>
        <p>Sistema de Vigilância Pública — Detecção Inteligente</p>
    </div>
    <div class="container">
        <div class="status-sistema">
            <span class="status-indicador"></span>
            <strong>SISTEMA ONLINE — IA ATIVA</strong> | Pronto para analisar imagens
        </div>

        <div class="card">
            <h2>📷 ENVIAR IMAGEM PARA ANÁLISE</h2>
            <label class="upload-area" for="arquivo">
                <p style="font-size: 18px; color: #88ccff;">Clique aqui ou arraste uma imagem</p>
                <p style="font-size: 13px; color: #777; margin-top: 8px;">Formatos aceitos: JPG, JPEG, PNG</p>
            </label>
            <input type="file" id="arquivo" accept=".jpg,.jpeg,.png" onchange="analisarImagem()">
            <center><button class="btn" onclick="document.getElementById('arquivo').click()">SELECIONAR IMAGEM</button></center>
            
            <img id="preview" class="img-preview" alt="Pré-visualização">
            
            <div id="resultado" class="resultado">
                <h3 id="titulo-resultado"></h3>
                <div id="detalhes" class="detalhes"></div>
            </div>
        </div>
    </div>

    <script>
        async function analisarImagem() {
            const input = document.getElementById('arquivo');
            const arquivo = input.files[0];
            if (!arquivo) return;

            // Mostrar pré-visualização
            const preview = document.getElementById('preview');
            preview.src = URL.createObjectURL(arquivo);
            preview.style.display = 'block';

            const formData = new FormData();
            formData.append('imagem', arquivo);

            const resultadoDiv = document.getElementById('resultado');
            const tituloDiv = document.getElementById('titulo-resultado');
            const detalhesDiv = document.getElementById('detalhes');
            
            resultadoDiv.style.display = 'none';
            resultadoDiv.className = 'resultado';
            tituloDiv.innerHTML = '🔄 Analisando...';
            resultadoDiv.style.display = 'block';
            resultadoDiv.className = 'resultado sucesso';

            try {
                const resposta = await fetch('/detectar', {
                    method: 'POST',
                    body: formData
                });
                const dados = await resposta.json();
                
                resultadoDiv.style.display = 'block';
                detalhesDiv.innerHTML = '';
                
                if (dados.status === 'sucesso') {
                    tituloDiv.innerHTML = '✅ IMAGEM ANALISADA';
                    resultadoDiv.className = 'resultado sucesso';
                    
                    if (dados.deteccoes.length > 0) {
                        let temArmaFogo = dados.deteccoes.some(d => 
                            d.classe.toLowerCase().includes('gun') || 
                            d.classe.toLowerCase().includes('pistol') ||
                            d.classe.toLowerCase().includes('rifle') ||
                            d.classe.toLowerCase().includes('firearm')
                        );
                        let temArmaBranca = dados.deteccoes.some(d => 
                            d.classe.toLowerCase().includes('knife') || 
                            d.classe.toLowerCase().includes('blade')
                        );
                        
                        if (temArmaFogo) {
                            tituloDiv.innerHTML = '🔴 ALERTA — ARMA DE FOGO DETECTADA';
                            resultadoDiv.className = 'resultado alerta-vermelho';
                        } else if (temArmaBranca) {
                            tituloDiv.innerHTML = '🟠 ATENÇÃO — ARMA BRANCA DETECTADA';
                            resultadoDiv.className = 'resultado alerta-laranja';
                        } else {
                            tituloDiv.innerHTML = '🟡 OBJETOS DETECTADOS';
                            resultadoDiv.className = 'resultado alerta-amarelo';
                        }
                        
                        detalhesDiv.innerHTML += `<strong>📊 ${dados.deteccoes.length} detecção(ões) encontrada(s):</strong><br><br>`;
                        dados.deteccoes.forEach(d => {
                            detalhesDiv.innerHTML += `🔍 <strong>${d.classe}</strong> — Confiança: ${d.confianca}%<br>`;
                        });
                    } else {
                        tituloDiv.innerHTML = '✅ SEM DETECÇÕES DE RISCO';
                        detalhesDiv.innerHTML = 'Nenhum objeto de risco identificado na imagem.';
                    }
                } else {
                    tituloDiv.innerHTML = '⚠️ ERRO';
                    detalhesDiv.innerHTML = dados.mensagem || 'Não foi possível analisar a imagem.';
                }
            } catch (erro) {
                tituloDiv.innerHTML = '⚠️ ERRO DE CONEXÃO';
                detalhesDiv.innerHTML = erro.message;
            }
        }
    </script>
</body>
</html>
"""

# Rota de detecção
@app.post("/detectar")
async def detectar(imagem: UploadFile = File(...)):
    if modelo is None:
        return {"status": "erro", "mensagem": "Modelo de IA não carregado no servidor"}
    
    try:
        conteudo = await imagem.read()
        img = Image.open(io.BytesIO(conteudo))
        
        # Executar detecção
        resultados = modelo(img, verbose=False)[0]
        
        deteccoes = []
        for obj in resultados.boxes:
            classe_id = int(obj.cls[0])
            classe = nomes_das_classes.get(classe_id, f"classe_{classe_id}")
            confianca = round(float(obj.conf[0]) * 100, 1)
            deteccoes.append({
                "classe": classe,
                "confianca": confianca
            })
        
        return {"status": "sucesso", "deteccoes": deteccoes}
    
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
