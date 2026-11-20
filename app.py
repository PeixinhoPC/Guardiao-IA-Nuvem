from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from ultralytics import YOLO
import uvicorn

app = FastAPI()
modelo = YOLO("best.pt")

@app.post("/detectar")
async def detectar(imagem: UploadFile = File(...)):
    try:
        resultado = modelo(imagem.file.read())[0]
        deteccoes = []
        for obj in resultado.boxes:
            deteccoes.append({
                "classe": modelo.names[int(obj.cls[0])],
                "confianca": float(obj.conf[0]),
                "coordenadas": [float(x) for x in obj.xyxy[0]]
            })
        return {"status": "ok", "deteccoes": deteccoes}
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
