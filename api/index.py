from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.environ.get("MONGO_URI")

@app.get("/api/orders")
async def get_user_orders(user_id: int = Query(..., description="Telegram User ID")):
    if not MONGO_URI:
        return []

    client = AsyncIOMotorClient(MONGO_URI)
    db = client['shop_database']
    orders_col = db['orders']
    
    # Chỉ truy vấn dữ liệu theo đúng user_id của khách (Bảo mật tuyệt đối)
    cursor = orders_col.find({"user_id": user_id}).sort("created_at", -1).limit(30)
    orders = await cursor.to_list(length=30)
    
    result = []
    for o in orders:
        items_data = []
        for item in o.get("items", []):
            items_data.append({
                "carrier": item.get("carrier", ""),
                "spx_code": item.get("spx_code", ""),
                "spx_stage": item.get("spx_stage", ""),
                "advance_payment": item.get("advance_payment", 0),
                "tracking_history": item.get("tracking_history", [])
            })
            
        result.append({
            "order_id": o.get("order_id", ""),
            "status": o.get("status", ""),
            "product_name": o.get("product_name", ""),
            "price": o.get("price", 0),
            "created_at": o["created_at"].strftime("%d/%m/%Y %H:%M") if "created_at" in o else "",
            "receiver_name": o.get("receiver_name", ""),
            "phone": o.get("phone", ""),
            "address": o.get("address", ""),
            "note": o.get("note", ""),
            "items": items_data
        })
        
    return result
