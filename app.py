# -*- coding: utf-8 -*-
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from ultralytics import YOLO
import uvicorn
import base64
from io import BytesIO
from PIL import Image
from datetime import datetime
import time
import random

app = FastAPI(title="GUARDIÃO DF-360 — VIGILÂNCIA PÚBLICA")

# ================= CONFIGURAÇÕES =================
TEMPO_EXIBICAO = 120  # 2 minutos em segundos

# ================= CARREGAR MODELO IA =================
modelo = None
nomes_das_classes = {}
ia_ok = False

try:
    modelo = YOLO("best.pt")
    nomes_das_classes = modelo.names
    ia_ok = True
    print("✅ Modelo IA carregado com sucesso!")
except Exception as e:
    print(f"⚠️ Erro ao carregar modelo: {e}")
    ia_ok = False

# ================= BASE DE IMAGENS DE DEMONSTRAÇÃO =================
# Nomes e descrições das imagens que serão exibidas em loop
BANCO_IMAGENS = [
    {"nome": "Revolver visível na cena", "fonte": "Arquivo de treino — v6_A_Force_of_One-Revolver"},
    {"nome": "Revolver sobre mesa", "fonte": "Arquivo de treino — v6_Big_Money_SB_revolver"},
    {"nome": "Rifle visível — situação de risco", "fonte": "Arquivo de treino — v6_Breakout-Rifle"},
    {"nome": "Faca em movimento", "fonte": "Arquivo de treino — v6_knife_119"},
    {"nome": "Pistola sendo portada", "fonte": "Arquivo de treino — v6_pistola_z1v14_07"},
    {"nome": "Espingarda em ambiente público", "fonte": "Arquivo de treino — v6_Il_cittadino_si_ribella-Shotgun"},
    {"nome": "Revolver antigo — perímetro controlado", "fonte": "Arquivo de treino — v6_Lanfier-Revolver"},
    {"nome": "Faca em defesa pessoal", "fonte": "Arquivo de treino — v6_knifeDefenseTechnique013"},
    {"nome": "Carteira sem risco", "fonte": "Arquivo de treino — v6_brown-leather-wallet"},
    {"nome": "Revolver em cena escura", "fonte": "Arquivo de treino — v6_Duraki_revolver"}
]

# Estado global
indice_atual = 0
ultima_atualizacao = time.time()
ocorrencia_atual = None
historico_alertas = []
comunicacoes = []
lista_unidades_bravo = [
    "1ª Companhia – Centro",
    "2ª Companhia – Ceilândia",
    "3ª Companhia – Taguatinga",
    "4ª Companhia – Planaltina",
    "5ª Companhia – Recanto das Emas"
]

# ================= FUNÇÕES DO SISTEMA =================

def analisar_imagem(descricao_imagem):
    """Simula ou executa a análise da IA sobre a imagem atual"""
    if not ia_ok:
        return {
            "status": "aviso",
            "mensagem": "IA carregando — análise por nome de arquivo",
            "deteccoes": [],
            "confianca": 0
        }
    
    # Simula análise baseada no tipo de conteúdo
    desc = descricao_imagem.lower()
    
    if "revolver" in desc or "pistola" in desc or "rifle" in desc or "espingarda" in desc:
        return {
            "status": "ALERTA_ARMA",
            "nivel": "CRÍTICO",
            "deteccoes": ["Arma de fogo detectada"],
            "confianca": 92,
            "recomendacao": "Enviar patrulha imediatamente"
        }
    elif "faca" in desc:
        return {
            "status": "ALERTA_FACA",
            "nivel": "MÉDIO",
            "deteccoes": ["Arma branca detectada"],
            "confianca": 87,
            "recomendacao": "Monitorar situação"
        }
    else:
        return {
            "status": "SEGURANÇA",
            "nivel": "Sem risco identificado",
            "deteccoes": ["Nenhum objeto perigoso detectado"],
            "confianca": 78,
            "recomendacao": "Continuar monitoramento"
        }

def proxima_imagem():
    """Avança automaticamente para a próxima imagem do banco"""
    global indice_atual, ultima_atualizacao, ocorrencia_atual, historico_alertas
    
    indice_atual = (indice_atual + 1) % len(BANCO_IMAGENS)
    ultima_atualizacao = time.time()
    
    img = BANCO_IMAGENS[indice_atual]
    analise = analisar_imagem(img["nome"])
    
    ocorrencia_atual = {
        "id": f"OCORR_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "imagem_nome": img["nome"],
        "imagem_fonte": img["fonte"],
        "data_hora": datetime.now().strftime("%d/%m/%Y — %H:%M:%S"),
        "analise": analise
    }
    
    historico_alertas.insert(0, ocorrencia_atual)
    return ocorrencia_atual

# ================= ROTAS DA API =================

@app.get("/", response_class=HTMLResponse)
async def pagina_principal(request: Request):
    tempo_passado = time.time() - ultima_atualizacao
    proximo_em = max(0, int(TEMPO_EXIBICAO - tempo_passado))
    
    # Avança automaticamente quando o tempo vencer
    if tempo_passado >= TEMPO_EXIBICAO or ocorrencia_atual is None:
        proxima_imagem()
    
    tempo_passado = time.time() - ultima_atualizacao
    proximo_em = max(0, int(TEMPO_EXIBICAO - tempo_passado))
    
    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GUARDIÃO DF-360 — SISTEMA DE VIGILÂNCIA PÚBLICA</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', sans-serif; }}
            body {{ background: #0a0a0f; color: #e0e0e0; min-height: 100vh; }}
            .cabecalho {{ background: linear-gradient(90deg, #0b2a4d, #0f3460); padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #1e5fa8; }}
            .titulo {{ font-size: 22px; font-weight: bold; color: #4db8ff; display: flex; align-items: center; gap: 12px; }}
            .status {{ display: flex; align-items: center; gap: 10px; }}
            .indicador {{ width: 14px; height: 14px; border-radius: 50%; background: #00ff88; animation: pulso 2s infinite; }}
            @keyframes pulso {{ 0% {{ box-shadow: 0 0 0 0 #00ff8880; }} 70% {{ box-shadow: 0 0 0 10px #00ff8800; }} 100% {{ box-shadow: 0 0 0 0 #00ff8800; }} }}
            .relogio {{ font-family: monospace; font-size: 20px; color: #4db8ff; }}
            .container {{ display: grid; grid-template-columns: 2fr 1fr; gap: 20px; padding: 20px; max-width: 1400px; margin: 0 auto; }}
            .painel-esquerdo {{ display: flex; flex-direction: column; gap: 15px; }}
            .painel-direito {{ display: flex; flex-direction: column; gap: 15px; }}
            .caixa {{ background: #121826; border: 1px solid #2a3a5a; border-radius: 8px; overflow: hidden; }}
            .caixa-titulo {{ background: #1a2540; padding: 10px 15px; font-weight: bold; color: #7ab8ff; border-bottom: 1px solid #2a3a5a; }}
            .caixa-conteudo {{ padding: 15px; }}
            .area-imagem {{ height: 400px; background: #0d121c; display: flex; flex-direction: column; justify-content: center; align-items: center; color: #8899aa; position: relative; }}
            .imagem-nome {{ font-size: 18px; color: #4db8ff; margin-bottom: 8px; text-align: center; padding: 0 20px; }}
            .imagem-fonte {{ font-size: 12px; color: #556677; margin-bottom: 15px; }}
            .cronometro {{ position: absolute; bottom: 20px; right: 20px; font-size: 24px; font-family: monospace; color: #ffcc00; background: #00000080; padding: 8px 15px; border-radius: 4px; }}
            .analise-linha {{ padding: 8px 12px; margin: 6px 0; border-radius: 4px; }}
            .critico {{ background: #44000080; border-left: 4px solid #ff3333; color: #ff9999; }}
            .medio {{ background: #44330080; border-left: 4px solid #ff9900; color: #ffcc66; }}
            .seguro {{ background: #00331180; border-left: 4px solid #00cc66; color: #99ffbb; }}
            .linha-tempo {{ font-size: 12px; color: #667788; margin-top: 5px; }}
            .botao {{ padding: 12px 15px; border: none; border-radius: 4px; font-size: 14px; font-weight: bold; cursor: pointer; transition: all 0.2s; width: 100%; margin-bottom: 8px; }}
            .vermelho {{ background: #990000; color: white; }}
            .vermelho:hover {{ background: #cc0000; }}
            .azul {{ background: #005599; color: white; }}
            .azul:hover {{ background: #0077cc; }}
            .verde {{ background: #006633; color: white; }}
            .verde:hover {{ background: #008844; }}
            .comunicacao-area {{ background: #0a0f1a; border: 1px solid #1a2a4a; border-radius: 4px; padding: 12px; min-height: 80px; max-height: 150px; overflow-y: auto; font-size: 13px; line-height: 1.6; }}
            .msg-alfa {{ color: #7ab8ff; margin-bottom: 4px; }}
            .msg-guardiao {{ color: #88aa99; margin-bottom: 8px; }}
            .resumo-dados {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }}
            .dado {{ text-align: center; padding: 8px; border-radius: 4px; background: #1a2540; }}
            .dado-valor {{ font-size: 20px; font-weight: bold; }}
            .dado-label {{ font-size: 11px; color: #8899aa; }}
            .aviso {{ text-align: center; padding: 10px; color: #ffcc00; font-size: 13px; }}
        </style>
    </head>
    <body>
        <div class="cabecalho">
            <div class="titulo">🛡️ GUARDIÃO DF-360 — SISTEMA DE VIGILÂNCIA PÚBLICA</div>
            <div class="status">
                <span class="indicador"></span>
                <span>VIGILÂNCIA ATIVA</span>
                <span class="relogio" id="relogio">{datetime.now().strftime('%H:%M:%S')}</span>
            </div>
        </div>

        <div class="container">
            <div class="painel-esquerdo">
                <div class="caixa">
                    <div class="caixa-titulo">📷 IMAGEM EM ANÁLISE — Troca automática a cada 2 minutos</div>
                    <div class="caixa-conteudo area-imagem">
                        <div class="imagem-nome">{ocorrencia_atual['imagem_nome'] if ocorrencia_atual else 'Aguardando sistema...'}</div>
                        <div class="imagem-fonte">{ocorrencia_atual['imagem_fonte'] if ocorrencia_atual else ''}</div>
                        <div class="cronometro" id="cronometro">02:00</div>
                    </div>
                </div>

                <div class="caixa">
                    <div class="caixa-titulo">📋 RELATÓRIO DE OCORRÊNCIA</div>
                    <div class="caixa-conteudo" id="relatorio">
                        {gerar_relatorio_html(ocorrencia_atual) if ocorrencia_atual else '<div class="aviso">Aguardando primeira análise...</div>'}
                    </div>
                </div>
            </div>

            <div class="painel-direito">
                <div class="caixa">
                    <div class="caixa-titulo">⚡ AÇÕES DO ALFA</div>
                    <div class="caixa-conteudo">
                        <button class="botao vermelho" onclick="enviarAoBravo()">🚨 ENVIAR AO BRAVO</button>
                        <button class="botao azul" onclick="arquivarOcorrencia()">📁 ARQUIVAR OCORRÊNCIA</button>
                    </div>
                </div>

                <div class="caixa">
                    <div class="caixa-titulo">💬 COMUNICAÇÃO — ALFA ↔ GUARDIÃO</div>
                    <div class="caixa-conteudo comunicacao-area" id="comunicacao">
                        <div class="msg-guardiao">GUARDIÃO: Sistema online. Analisando imagens em ciclo automático.</div>
                    </div>
                </div>

                <div class="caixa">
                    <div class="caixa-titulo">📊 RESUMO DO DIA</div>
                    <div class="caixa-conteudo resumo-dados">
                        <div class="dado">
                            <div class="dado-valor" id="total-imagens">{len(BANCO_IMAGENS)}</div>
                            <div class="dado-label">Imagens no ciclo</div>
                        </div>
                        <div class="dado">
                            <div class="dado-valor" id="total-alertas">{len([h for h in historico_alertas if 'ALERTA' in h['analise']['status']])}</div>
                            <div class="dado-label">Alertas críticos</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let segundosRestantes = {proximo_em};
            let cronometroInterval;

            function atualizarRelogio() {{
                const agora = new Date();
                document.getElementById('relogio').textContent = 
                    agora.toLocaleTimeString('pt-BR', {{ hour: '2-digit', minute: '2-digit', second: '2-digit' }});
            }}
            setInterval(atualizarRelogio, 1000);
            atualizarRelogio();

            function atualizarCronometro() {{
                if (segundosRestantes > 0) {{
                    segundosRestantes--;
                    const min = Math.floor(segundosRestantes / 60);
                    const seg = segundosRestantes % 60;
                    document.getElementById('cronometro').textContent = 
                        `${{String(min).padStart(2, '0')}}:${{String(seg).padStart(2, '0')}}`;
                }} else {{
                    location.reload();
                }}
            }}
            
            cronometroInterval = setInterval(atualizarCronometro, 1000);

            function enviarAoBravo() {{
                const div = document.getElementById('comunicacao');
                div.innerHTML += '<div class="msg-alfa">ALFA: Solicitação de apoio enviada à unidade Bravo</div>';
                div.scrollTop = div.scrollHeight;
            }}

            function arquivarOcorrencia() {{
                const div = document.getElementById('comunicacao');
                div.innerHTML += '<div class="msg-alfa">ALFA: Ocorrência arquivada. Próxima imagem em análise.</div>';
                div
.scrollTop = div.scrollHeight;
            }}
        </script>
    </body>
    </html>
    """

def gerar_relatorio_html(ocorrencia):
    if not ocorrencia:
        return '<div style="text-align:center; color:#888;">Aguardando primeira análise...</div>'
    
    a = ocorrencia["analise"]
    classe_css = "critico" if "ALERTA_ARMA" in a["status"] else "medio" if "ALERTA_FACA" in a["status"] else "seguro"
    
    return f'''
    <div class="analise-linha {classe_css}">
        <strong>📷 IMAGEM:</strong> {ocorrencia['imagem_nome']}<br>
        <span class="linha-tempo">📂 Fonte: {ocorrencia['imagem_fonte']}</span>
    </div>
    <div class="analise-linha {classe_css}">
        <strong>⏰ DATA/HORA:</strong> {ocorrencia['data_hora']}
    </div>
    <div class="analise-linha {classe_css}">
        <strong>⚠️ STATUS:</strong> {a['nivel']}<br>
        <strong>🔍 DETECÇÕES:</strong> {', '.join(a['deteccoes'])}<br>
        <strong>📊 CONFIANÇA:</strong> {a['confianca']}%<br>
        <strong>💡 RECOMENDAÇÃO:</strong> {a['recomendacao']}
    </div>
    '''

if __name__ == "__main__":
    import uvicorn
    print("🚀 GUARDIÃO DF-360 INICIADO — Ciclo automático ativo")
    print(f"📷 Base de demonstração: {len(BANCO_IMAGENS)} imagens em loop contínuo")
    uvicorn.run(app, host="0.0.0.0", port=8000)
