# -*- coding: utf-8 -*-
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from datetime import datetime
import time

app = FastAPI(title="GUARDIÃO DF-360 — VIGILÂNCIA PÚBLICA")

# ================= CONFIGURAÇÕES =================
TEMPO_EXIBICAO = 120  # 2 minutos em segundos

# ================= BASE DE IMAGENS DE DEMONSTRAÇÃO =================
BANCO_IMAGENS = [
    {"nome": "Revolver visível na cena", "fonte": "v6_A_Force_of_One-Revolver",
     "status": "ALERTA_ARMA", "nivel": "CRÍTICO", "confianca": 92,
     "deteccoes": "Arma de fogo detectada", "recomendacao": "Enviar patrulha imediatamente"},
    
    {"nome": "Revolver sobre mesa", "fonte": "v6_Big_Money_SB_revolver",
     "status": "ALERTA_ARMA", "nivel": "CRÍTICO", "confianca": 89,
     "deteccoes": "Arma de fogo detectada", "recomendacao": "Manter observação"},
    
    {"nome": "Rifle visível — situação de risco", "fonte": "v6_Breakout-Rifle",
     "status": "ALERTA_ARMA", "nivel": "CRÍTICO", "confianca": 94,
     "deteccoes": "Arma de fogo — longo alcance", "recomendacao": "Isolar área imediatamente"},
    
    {"nome": "Faca em movimento", "fonte": "v6_knife_119",
     "status": "ALERTA_FACA", "nivel": "MÉDIO", "confianca": 87,
     "deteccoes": "Arma branca detectada", "recomendacao": "Monitorar comportamento"},
    
    {"nome": "Pistola sendo portada", "fonte": "v6_pistola_z1v14_07",
     "status": "ALERTA_ARMA", "nivel": "CRÍTICO", "confianca": 91,
     "deteccoes": "Arma de fogo detectada", "recomendacao": "Intervir com apoio"},
    
    {"nome": "Espingarda em ambiente público", "fonte": "v6_Il_cittadino_si_ribella-Shotgun",
     "status": "ALERTA_ARMA", "nivel": "CRÍTICO", "confianca": 93,
     "deteccoes": "Arma de fogo — ambiente público", "recomendacao": "Evacuar área próxima"},
    
    {"nome": "Revolver antigo — perímetro controlado", "fonte": "v6_Lanfier-Revolver",
     "status": "SEGURANÇA", "nivel": "Sem risco imediato", "confianca": 76,
     "deteccoes": "Objeto identificado sem ameaça ativa", "recomendacao": "Continuar observação"},
    
    {"nome": "Faca em defesa pessoal", "fonte": "v6_knifeDefenseTechnique013",
     "status": "ALERTA_FACA", "nivel": "MÉDIO", "confianca": 82,
     "deteccoes": "Arma branca detectada", "recomendacao": "Avaliar necessidade de intervenção"},
    
    {"nome": "Carteira — sem risco", "fonte": "v6_brown-leather-wallet",
     "status": "SEGURANÇA", "nivel": "Sem risco identificado", "confianca": 78,
     "deteccoes": "Nenhum objeto perigoso detectado", "recomendacao": "Continuar monitoramento"},
    
    {"nome": "Revolver em cena escura", "fonte": "v6_Duraki_revolver",
     "status": "ALERTA_ARMA", "nivel": "CRÍTICO", "confianca": 85,
     "deteccoes": "Arma de fogo — visibilidade reduzida", "recomendacao": "Reforçar iluminação e observação"}
]

# Estado global
indice_atual = 0
ultima_atualizacao = time.time()
ocorrencia_atual = None
historico_alertas = []

# ================= FUNÇÕES =================

def proxima_imagem():
    global indice_atual, ultima_atualizacao, ocorrencia_atual, historico_alertas
    
    indice_atual = (indice_atual + 1) % len(BANCO_IMAGENS)
    ultima_atualizacao = time.time()
    
    img = BANCO_IMAGENS[indice_atual]
    
    ocorrencia_atual = {
        "id": f"OCORR_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "imagem_nome": img["nome"],
        "imagem_fonte": img["fonte"],
        "data_hora": datetime.now().strftime("%d/%m/%Y — %H:%M:%S"),
        "status": img["status"],
        "nivel": img["nivel"],
        "confianca": img["confianca"],
        "deteccoes": img["deteccoes"],
        "recomendacao": img["recomendacao"]
    }
    
    historico_alertas.insert(0, ocorrencia_atual)
    return ocorrencia_atual

def gerar_relatorio_html(ocorrencia):
    if not ocorrencia:
        return '<div style="text-align:center; color:#888;">Aguardando primeira análise...</div>'
    
    st = ocorrencia["status"]
    classe_css = "critico" if "ALERTA_ARMA" in st else "medio" if "ALERTA_FACA" in st else "seguro"
    
    return f'''
    <div class="analise-linha {classe_css}">
        <strong>📷 IMAGEM:</strong> {ocorrencia['imagem_nome']}<br>
        <span class="linha-tempo">📂 Fonte: {ocorrencia['imagem_fonte']}</span>
    </div>
    <div class="analise-linha {classe_css}">
        <strong>⏰ DATA/HORA:</strong> {ocorrencia['data_hora']}
    </div>
    <div class="analise-linha {classe_css}">
        <strong>⚠️ STATUS:</strong> {ocorrencia['nivel']}<br>
        <strong>🔍 DETECÇÕES:</strong> {ocorrencia['deteccoes']}<br>
        <strong>📊 CONFIANÇA:</strong> {ocorrencia['confianca']}%<br>
        <strong>💡 RECOMENDAÇÃO:</strong> {ocorrencia['recomendacao']}
    </div>
    '''

# ================= ROTAS =================

@app.get("/", response_class=HTMLResponse)
async def pagina_principal(request: Request):
    global ocorrencia_atual, ultima_atualizacao
    
    tempo_passado = time.time() - ultima_atualizacao
    
    # Primeira carga OU tempo vencido → avança
    if ocorrencia_atual is None or tempo_passado >= TEMPO_EXIBICAO:
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
            .painel-esquerdo, .painel-direito {{ display: flex; flex-direction: column; gap: 15px; }}
            .caixa {{ background: #121826; border: 1px solid #2a3a5a; border-radius: 8px; overflow: hidden; }}
            .caixa-titulo {{ background: #1a2540; padding: 10px 15px; font-weight: bold; color: #7ab8ff; border-bottom: 1px solid #2a3a5a; }}
            .caixa-conteudo {{ padding: 15px; }}
            .area-imagem {{ height: 300px; background: #0d121c; display: flex; flex-direction: column; justify-content: center; align-items: center; color: #8899aa; position: relative; text-align: center; padding: 20px; }}
            .imagem-nome {{ font-size: 18px; color: #4db8ff; margin-bottom: 8px; }}
            .imagem-fonte {{ font-size: 12px; color: #556677; margin-bottom: 15px; }}
            .cronometro {{ position: absolute; bottom: 20px; right: 20px; font-size: 24px; font-family: monospace; color: #ffcc00; background: #00000080; padding: 8px 15px; border-radius: 4px; }}
            .analise-linha {{ padding: 10px 12px; margin: 8px 0; border-radius: 4px; line-height: 1.6; }}
            .critico {{ background: #44000080; border-left: 4px solid #ff3333; color: #ff9999; }}
            .medio {{ background: #44330080; border-left: 4px solid #ff9900; color: #ffcc66; }}
            .seguro {{ background: #00331180; border-left: 4px solid #00cc66; color: #99ffbb; }}
            .linha-tempo {{ font-size: 12px; color: #667788; margin-top: 5px; display: block; }}
            .comunicacao-area {{ background: #0a0f1a; border: 1px solid #1a2a4a; border-radius: 4px; padding: 12px; min-height: 80px; max-height: 150px; overflow-y: auto; font-size: 13px; line-height: 1.8; }}
            .msg-guardiao {{ color: #88aa99; margin-bottom: 8px; }}
            .resumo-dados {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }}
            .dado {{ text-align: center; padding: 12px 8px; border-radius: 4px; background: #1a2540; }}
            .dado-valor {{ font-size: 24px; font-weight: bold; color: #4db8ff; }}
            .dado-label {{ font-size: 12px; color: #8899aa; margin-top: 4px; }}
        </style>
    </head>
    <body>
        <div class="cabecalho">
            <div class="titulo">🛡️ GUARDIÃO DF-360 — SISTEMA DE VIGILÂNCIA PÚBLICA</div>
            <div class="status">
                <span class="indicador"></span>
                <span>VIGILÂNCIA ATIVA</span>
                <span class="relogio" id="relogio"></span>
            </div>
        </div>

        <div class="container">
            <div class="painel-esquerdo">
                <div class="caixa">
                    <div class="caixa-titulo">📷 IMAGEM EM ANÁLISE — Troca automática a cada 2 minutos</div>
                    <div class="caixa-conteudo area-imagem">
                        <div class="imagem-nome">{ocorrencia_atual['imagem_nome']}</div>
                        <div class="imagem-fonte">{ocorrencia_atual['imagem_fonte']}</div>
                        <div class="cronometro" id="cronometro">02:00</div>
                    </div>
                </div>

                <div class="caixa">
                    <div class="caixa-titulo">📋 RELATÓRIO DE OCORRÊNCIA</div>
                    <div class="caixa-conteudo">
                        {gerar_relatorio_html(ocorrencia_atual)}
                    </div>
                </div>
            </div>

            <div class="painel-direito">
                <div class="caixa">
                    <div class="caixa-titulo">💬 COMUNICAÇÃO — ALFA ↔ GUARDIÃO</div>
                    <div class="caixa-conteudo comunicacao-area">
                        <div class="msg-guardiao">GUARDIÃO: Sistema online. Ciclo automático iniciado. Aguardando solicitações.</div>
                    </div>
                </div>

                <div class="caixa">
                    <div class="caixa-titulo">📊 RESUMO DO CICLO</div>
                    <div class="caixa-conteudo resumo-dados">
                        <div class="dado">
                            <div class="dado-valor">{len(BANCO_IMAGENS)}</div>
                            <div class="dado-label">Imagens no ciclo</div>
                        </div>
                        <div class="dado">
                            <div class="dado-valor">{len([h for h in historico_alertas if 'ALERTA' in h['status']])}</div>
                            <div class="dado-label">Alertas emitidos</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let segundosRestantes = {proximo_em};

            function atualizarRelogio() {{
                const agora = new Date();
                document.getElementById('relogio').textContent = agora.toLocaleTimeString('pt-BR');
            }}
            setInterval(atualizarRelogio, 1000);
            atualizarRelogio();

            function atualizarCronometro() {{
                if (segundosRestantes > 0) {{
                    segundosRestantes--;
                    const min = Math.floor(segundosRestantes / 60);
                    const seg = segundosRestantes % 60;
                    document.getElementById('cronometro').textContent = 
                        String(min).padStart(2, '0') + ':' + String(seg).padStart(2, '0');
                }} else {{
                    location.reload();
                }}
            }}
            setInterval(atualizarCronometro, 1000);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    print("🚀 GUARDIÃO DF-360 INICIADO — Ciclo automático ativo")
    print(f"📷 Base de demonstração: {len(BANCO_IMAGENS)} imagens em loop contínuo")
    uvicorn.run(app, host="0.0.0.0", port=8000)
