import os
import ssl
from contextlib import asynccontextmanager

import redis.asyncio as redis
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
from model.db import Base, get_db, Engine
from utils.hot_spot import MunicipalHotspotRanker
from utils.save_pa_token import PaTokenManager
from routers import mobile, web
from fastapi.responses import HTMLResponse

pa_token_manager = PaTokenManager()
Base.metadata.create_all(Engine)
load_dotenv()
hotspot_ranker = None
CIPHER: str = 'AES128-SHA:AES256-SHA:AES256-SHA256'
CONTENT = ssl._create_unverified_context()
CONTENT.set_ciphers(CIPHER)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    """应用生命周期事件处理器"""
    global redis_client, hotspot_ranker
    # 应用启动时获取初始 token 和建立Redis连接
    try:
        # 初始化Redis客户端
        redis_client = redis.from_url(REDIS_URL)

        # 获取初始token
        a = await pa_token_manager.refresh_token()
        print(a)
        print("Initial token fetched successfully")

        # 初始化热度分析器并从数据库加载数据
        hotspot_ranker = MunicipalHotspotRanker(similarity_threshold=0.6)
        # 从数据库加载历史数据
        db_session = next(get_db())
        try:
            hotspot_ranker.load_from_database(db_session)
        finally:
            db_session.close()
        print("Hotspot ranker initialized and loaded from database")
    except Exception as e:
        print(f"Failed to initialize: {e}")
        import traceback
        traceback.print_exc()
        # 如果加载失败，至少创建一个空的分析器
        if hotspot_ranker is None:
            hotspot_ranker = MunicipalHotspotRanker(similarity_threshold=0.6)

    yield

    # 应用关闭时的清理工作
    if redis_client:
        await redis_client.close()
    print("Application shutting down...")


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源，生产环境应指定具体源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)

app.include_router(mobile.app)
app.include_router(web.app)

@app.get("/")
async def root():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'index.html')
    html_content = ''
    with open(html_path) as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)


if __name__ == '__main__':
    import os

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host=host, port=port)