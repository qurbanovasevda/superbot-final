"""Run Phase 2 on a separate port/database; run.py remains the Phase 1 entrypoint."""
import uvicorn
from app.main import create_app

if __name__=='__main__':
    uvicorn.run(create_app(data_mode='live'),host='127.0.0.1',port=8001)
