from fastapi import FastAPI

app = FastAPI()


@app.get("/livez")
def livez():
    return {"status": "ok"}
