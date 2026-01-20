# serve.py
from fastapi import FastAPI, Request
import uvicorn

app = FastAPI()

@app.post("/invocations")
async def invocations(req: Request):
    body = await req.body()
    return body.decode("utf-8")

@app.get("/ping")
def ping():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
