from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from pymongo import MongoClient
import os
import re
import requests
import asyncio
from datetime import datetime, timedelta

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.environ.get("MONGO_URI", "")
client = MongoClient(MONGO_URI) if MONGO_URI else None

# TỪ ĐIỂN TỌA ĐỘ NÂNG CẤP (Bao gồm các HUB trong ảnh của bạn)
HUB_LOCATIONS = {
    "thâm quyến": {"lat": 22.5431, "lng": 114.0579},
    "nghĩa ô": {"lat": 29.3068, "lng": 120.0750},
    "bw soc": {"lat": 11.0067, "lng": 106.5139},
    "hn mê linh soc": {"lat": 21.1828, "lng": 105.7142},
    "từ sơn soc": {"lat": 21.1167, "lng": 105.9500},
    "bn a mega": {"lat": 21.0828, "lng": 105.9767}, # KCN VSIP Bắc Ninh
    "21-hni thanh tri": {"lat": 20.9634, "lng": 105.8156}, # Thanh Trì, HN
    "30-tha": {"lat": 19.3833, "lng": 105.7833}, # Tĩnh Gia 2, Thanh Hóa
    "tĩnh gia 2": {"lat": 19.3833, "lng": 105.7833},
}

class TrackingRequest(BaseModel):
    trackings: List[str]

def extract_gmap_coords(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
        final_url = res.url
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', final_url)
        if match: return {"lat": float(match.group(1)), "lng": float(match.group(2))}
    except: pass
    return None

def guess_coordinates(text, fallback_lat=19.3833, fallback_lng=105.7833):
    if not text: return {"lat": fallback_lat, "lng": fallback_lng}
    try:
        text_lower = str(text).lower().strip()
        if "http" in text_lower and ("maps" in text_lower or "goo.gl" in text_lower):
            url_match = re.search(r'(https?://[^\s]+)', text)
            if url_match:
                coords = extract_gmap_coords(url_match.group(1))
                if coords: return coords
        for key, coords in HUB_LOCATIONS.items():
            if key in text_lower: return coords
    except: pass
    return {"lat": fallback_lat, "lng": fallback_lng}

# =====================================================================
# TỰ ĐỘNG CẬP NHẬT MỖI 1 PHÚT (AUTO SYNC BACKGROUND)
# =====================================================================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(auto_sync_spx_loop())

async def auto_sync_spx_loop():
    while True:
        try:
            if client:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Đang tự động Sync Lịch sử SPX...")
                sync_spx_logic()
        except Exception as e:
            print(f"Lỗi Auto Sync: {e}")
        await asyncio.sleep(60) # Chạy lại sau mỗi 60 giây

def sync_spx_logic():
    db = client['shop_database']
    query = {"status": {"$nin": ["Thành công", "Đã giao", "Đã hủy"]}}
    orders = list(db['orders'].find(query).sort("created_at", -1).limit(100))
    
    trackings_to_check = set()
    tracking_to_order_map = {}
    
    for o in orders:
        order_id = o.get("order_id")
        items = o.get("items", [])
        for idx, item in enumerate(items):
            spx_code = str(item.get("spx_code", "")).strip().upper()
            if spx_code.startswith("SPX") or spx_code.startswith("VN"):
                trackings_to_check.add(spx_code)
                if spx_code not in tracking_to_order_map:
                    tracking_to_order_map[spx_code] = []
                tracking_to_order_map[spx_code].append((order_id, idx))

    if not trackings_to_check: return

    target_url = "https://dodanhvu.dpdns.org/api/spx"
    payload = {"trackings": list(trackings_to_check)}
    headers = {"Content-Type": "application/json"}
    
    try:
        res = requests.post(target_url, json=payload, headers=headers, timeout=20)
        if res.status_code == 200:
            data = res.json()
            results = data.get("results", [])
            
            for r in results:
                t_code = r.get("tracking")
                records = r.get("records", []) 
                
                if not records: continue
                
                latest_record = records[0]
                new_status = latest_record.get("desc") or r.get("status")
                
                # CẤU TRÚC LẠI LỊCH SỬ Y HỆT WEB CỦA HỌ
                formatted_history = []
                for rec in records:
                    time_str = rec.get("time", "")
                    emoji = rec.get("emoji", "›")
                    desc = rec.get("desc", "")
                    current_loc = rec.get("currentLoc", "")
                    next_loc = rec.get("nextLoc", "")
                    
                    full_desc = f"<strong style='color:#fff;'>{emoji} {desc}</strong>"
                    if current_loc:
                        full_desc += f"<br><span style='color:#9ca3af;font-size:11.5px;display:inline-block;margin-top:3px'>📍 {current_loc}</span>"
                    if next_loc:
                        full_desc += f"<br><span style='color:#60a5fa;font-size:11.5px;display:inline-block;margin-top:3px'>→ {next_loc}</span>"
                        
                    formatted_history.append({"time": time_str, "description": full_desc})
                
                if t_code in tracking_to_order_map:
                    for oid, item_idx in tracking_to_order_map[t_code]:
                        db['orders'].update_one(
                            {"order_id": oid},
                            {"$set": {
                                f"items.{item_idx}.spx_stage": new_status,
                                f"items.{item_idx}.tracking_history": formatted_history
                            }}
                        )
    except Exception as e:
        pass


@app.get("/")
def home():
    return {"status": "Backend Python đang chạy mượt mà!"}

@app.get("/api/orders")
def get_user_orders(user_id: str = Query(...)):
    try:
        if not client: return JSONResponse(status_code=500, content={"detail": "Chưa kết nối MongoDB"})
        db = client['shop_database']
        try: uid = int(user_id)
        except: uid = user_id
        orders = list(db['orders'].find({"$or": [{"user_id": uid}, {"user_id": str(user_id)}]}).sort("created_at", -1).limit(30))
        result = []
        for o in orders:
            receiver_address = str(o.get("address", ""))
            recv_coords = guess_coordinates(receiver_address)
            raw_items = o.get("items")
            if not isinstance(raw_items, list): raw_items = []
            if len(raw_items) == 0:
                raw_items = [{"link": o.get("product_link", "Đơn hàng"), "carrier": o.get("carrier", "N/A"), "spx_code": o.get("spx_code", ""), "spx_stage": o.get("spx_stage", o.get("status", "")), "advance_payment": o.get("advance_payment", 0), "tracking_history": []}]

            items_data = []
            for item in raw_items:
                if not isinstance(item, dict): continue 
                current_stage = str(item.get("spx_stage") or o.get("status") or "Đang xử lý")
                cur_coords = guess_coordinates(current_stage, fallback_lat=recv_coords["lat"] + 0.05, fallback_lng=recv_coords["lng"] + 0.05)
                t_history = item.get("tracking_history")
                safe_history = []
                if isinstance(t_history, list):
                    for h in t_history:
                        if isinstance(h, dict): safe_history.append({"time": str(h.get("time", "")), "description": str(h.get("description", ""))})
                try: adv_pay = float(item.get("advance_payment", 0))
                except: adv_pay = 0
                items_data.append({"link": str(item.get("link", "")), "carrier": str(item.get("carrier", "")), "spx_code": str(item.get("spx_code", "")), "spx_stage": current_stage, "advance_payment": adv_pay, "tracking_history": safe_history, "current_lat": float(cur_coords["lat"]), "current_lng": float(cur_coords["lng"]), "receiver_lat": float(recv_coords["lat"]), "receiver_lng": float(recv_coords["lng"])})
                
            dt = o.get("created_at")
            if isinstance(dt, datetime): dt_str = dt.strftime("%d/%m/%Y %H:%M")
            else: dt_str = str(dt) if dt else datetime.now().strftime("%d/%m/%Y %H:%M")
            try: price_val = float(o.get("price", 0))
            except: price_val = 0
            result.append({"order_id": str(o.get("order_id", "")), "status": str(o.get("status", "Đang xử lý")), "product_name": str(o.get("product_name", "Đơn hàng mới")), "price": price_val, "created_at": dt_str, "receiver_name": str(o.get("receiver_name", "")), "phone": str(o.get("phone", "")), "address": receiver_address, "items": items_data})
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


@app.get("/api/live_location")
def get_live_location(order_id: str = Query(...)):
    """API Bản đồ: Tuyệt đối không nhầm lẫn vị trí Hiện tại và Điểm đến"""
    try:
        if not client: return {"lat": 19.3833, "lng": 105.7833, "status": "Không kết nối"}
        
        db = client['shop_database']
        order = db['orders'].find_one({"order_id": order_id})
        if not order: return {"error": "Not found"}
        
        items = order.get("items") or []
        db_address = str(order.get("address", "")).strip()
        current_stage = str(order.get("status", "")).strip()
        tracking_code = ""

        if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
            current_stage = str(items[0].get("spx_stage", current_stage)).strip()
            tracking_code = str(items[0].get("spx_code", "")).strip().upper()

        if tracking_code.startswith("SPX") or tracking_code.startswith("VN"):
            try:
                target_url = "https://dodanhvu.dpdns.org/api/spx"
                payload = {"trackings": [tracking_code]}
                res = requests.post(target_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
                
                if res.status_code == 200:
                    data = res.json()
                    results = data.get("results", [])
                    
                    if results and len(results) > 0:
                        spx_data = results[0]
                        records = spx_data.get("records", [])
                        exact_location = ""
                        
                        # QUY TẮC THÉP: Chặn đứng mọi từ khóa "nextLoc", chỉ lấy "currentLoc"
                        for rec in records:
                            if rec.get("currentLoc"):
                                exact_location = rec.get("currentLoc")
                                break # Lấy mốc mới nhất rồi dừng
                        
                        # Nếu không có currentLoc, quét mô tả để tìm kho đang đứng
                        if not exact_location:
                            for rec in records:
                                desc = rec.get("desc", "").lower()
                                if "đến kho" in desc or "tại" in desc or "xuất khỏi" in desc or "lấy hàng" in desc:
                                    exact_location = rec.get("desc")
                                    break
                                    
                        final_status = exact_location if exact_location else spx_data.get("status")
                        cur_coords = guess_coordinates(final_status)
                        
                        return {"lat": cur_coords["lat"], "lng": cur_coords["lng"], "status": final_status, "source": "WEB_API"}
            except Exception as e:
                pass

        location_text = db_address if db_address else current_stage
        if not location_text or location_text == "Đang xử lý":
            location_text = db_address

        cur_coords = guess_coordinates(location_text)
        return {"lat": cur_coords["lat"], "lng": cur_coords["lng"], "status": location_text, "source": "DATABASE"}

    except Exception as e:
        return {"lat": 19.3833, "lng": 105.7833, "status": "Lỗi"}


@app.post("/api/sync_spx_from_db")
def sync_spx_from_db(user_id: str = Query(None)):
    sync_spx_logic()
    return {"message": "Đã sync thủ công xong"}

@app.get("/api/stats")
def get_web_stats(user_id: int = Query(0)):
    try:
        if not client: return {"online": 1, "monthly": 1}
        db = client['shop_database']
        now = datetime.utcnow()
        current_month = now.strftime("%Y-%m")
        if user_id != 0:
            db['web_stats'].update_one({"user_id": user_id, "month": current_month}, {"$set": {"last_active": now}}, upsert=True)
        three_mins_ago = now - timedelta(minutes=3)
        return {"online": max(1, db['web_stats'].count_documents({"last_active": {"$gte": three_mins_ago}})), "monthly": max(1, db['web_stats'].count_documents({"month": current_month}))}
    except:
        return {"online": 1, "monthly": 1}def extract_gmap_coords(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
        final_url = res.url
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', final_url)
        if match: return {"lat": float(match.group(1)), "lng": float(match.group(2))}
    except: pass
    return None

def guess_coordinates(text, fallback_lat=19.3833, fallback_lng=105.7833):
    if not text: return {"lat": fallback_lat, "lng": fallback_lng}
    try:
        text_lower = str(text).lower().strip()
        if "http" in text_lower and ("maps" in text_lower or "goo.gl" in text_lower):
            url_match = re.search(r'(https?://[^\s]+)', text)
            if url_match:
                coords = extract_gmap_coords(url_match.group(1))
                if coords: return coords
        for key, coords in HUB_LOCATIONS.items():
            if key in text_lower: return coords
    except: pass
    return {"lat": fallback_lat, "lng": fallback_lng}

# =====================================================================
# TỰ ĐỘNG CHẠY NGẦM MỖI 1 PHÚT (AUTO SYNC BACKGROUND TASK)
# =====================================================================
@app.on_event("startup")
async def startup_event():
    # Khi server khởi động, kích hoạt vòng lặp chạy ngầm
    asyncio.create_task(auto_sync_spx_loop())

async def auto_sync_spx_loop():
    """Vòng lặp tự động quét Database và Web dodanhvu mỗi 60 giây"""
    while True:
        try:
            if client:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Đang tự động lấy dữ liệu SPX...")
                sync_spx_logic() # Gọi hàm đồng bộ
        except Exception as e:
            print(f"Lỗi Auto Sync: {e}")
        
        # Ngủ 60 giây rồi chạy lại
        await asyncio.sleep(60)

def sync_spx_logic():
    """Hàm xử lý lõi: Lấy mã -> Tra Web -> Lấy TOÀN BỘ Lịch sử -> Lưu DB"""
    db = client['shop_database']
    
    # Chỉ quét các đơn chưa hoàn thành để tiết kiệm tài nguyên
    # (Lọc bỏ các đơn Đã giao/Thành công/Đã hủy)
    query = {"status": {"$nin": ["Thành công", "Đã giao", "Đã hủy"]}}
    orders = list(db['orders'].find(query).sort("created_at", -1).limit(100))
    
    trackings_to_check = set()
    tracking_to_order_map = {}
    
    # Rút trích mã SPX/VN từ DB
    for o in orders:
        order_id = o.get("order_id")
        items = o.get("items", [])
        for idx, item in enumerate(items):
            spx_code = str(item.get("spx_code", "")).strip().upper()
            if spx_code.startswith("SPX") or spx_code.startswith("VN"):
                trackings_to_check.add(spx_code)
                if spx_code not in tracking_to_order_map:
                    tracking_to_order_map[spx_code] = []
                tracking_to_order_map[spx_code].append((order_id, idx))

    if not trackings_to_check:
        return # Không có mã nào cần kiểm tra

    # Bắn 1 request duy nhất sang web với tất cả các mã
    target_url = "https://dodanhvu.dpdns.org/api/spx"
    payload = {"trackings": list(trackings_to_check)}
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    
    try:
        res = requests.post(target_url, json=payload, headers=headers, timeout=20)
        if res.status_code == 200:
            data = res.json()
            results = data.get("results", [])
            
            for r in results:
                t_code = r.get("tracking")
                records = r.get("records", []) # Đây là mảng chứa toàn bộ "Lịch sử giao hàng"
                
                if not records:
                    continue
                
                # Lấy trạng thái mới nhất từ mảng records
                latest_record = records[0]
                new_status = latest_record.get("desc") or r.get("status")
                
                # FORMAT TOÀN BỘ LỊCH SỬ GIỐNG Y HỆT TRÊN WEB ĐỂ ĐẨY VÀO DB
                formatted_history = []
                for rec in records: # Duyệt qua toàn bộ mốc thời gian (vd: 11 mốc)
                    time_str = rec.get("time", "")
                    emoji = rec.get("emoji", "❯")
                    desc = rec.get("desc", "")
                    current_loc = rec.get("currentLoc", "")
                    
                    # Gộp mô tả và địa chỉ cụ thể vào làm 1 để hiển thị ra App đẹp nhất
                    full_desc = f"{emoji} {desc}"
                    if current_loc:
                        full_desc += f"<br>→ <small style='color:var(--text-sub)'>{current_loc}</small>"
                        
                    formatted_history.append({
                        "time": time_str,
                        "description": full_desc
                    })
                
                # Cập nhật ngược lại vào MongoDB
                if t_code in tracking_to_order_map:
                    for oid, item_idx in tracking_to_order_map[t_code]:
                        db['orders'].update_one(
                            {"order_id": oid},
                            {"$set": {
                                f"items.{item_idx}.spx_stage": new_status,
                                f"items.{item_idx}.tracking_history": formatted_history
                            }}
                        )
    except Exception as e:
        print(f"Lỗi khi tra dữ liệu Web: {e}")
# =====================================================================


@app.get("/")
def home():
    return {"status": "Backend Python đang chạy mượt mà!"}

@app.get("/api/orders")
def get_user_orders(user_id: str = Query(...)):
    try:
        if not client: return JSONResponse(status_code=500, content={"detail": "Chưa kết nối MongoDB"})
        db = client['shop_database']
        
        try: uid = int(user_id)
        except: uid = user_id
        
        orders = list(db['orders'].find({"$or": [{"user_id": uid}, {"user_id": str(user_id)}]}).sort("created_at", -1).limit(30))
        result = []
        
        for o in orders:
            receiver_address = str(o.get("address", ""))
            recv_coords = guess_coordinates(receiver_address)

            raw_items = o.get("items")
            if not isinstance(raw_items, list): raw_items = []
            if len(raw_items) == 0:
                raw_items = [{
                    "link": o.get("product_link", "Đơn hàng"),
                    "carrier": o.get("carrier", "N/A"),
                    "spx_code": o.get("spx_code", ""),
                    "spx_stage": o.get("spx_stage", o.get("status", "")),
                    "advance_payment": o.get("advance_payment", 0),
                    "tracking_history": []
                }]

            items_data = []
            for item in raw_items:
                if not isinstance(item, dict): continue 
                
                current_stage = str(item.get("spx_stage") or o.get("status") or "Đang xử lý")
                cur_coords = guess_coordinates(current_stage, fallback_lat=recv_coords["lat"] + 0.05, fallback_lng=recv_coords["lng"] + 0.05)
                
                t_history = item.get("tracking_history")
                safe_history = []
                if isinstance(t_history, list):
                    for h in t_history:
                        if isinstance(h, dict):
                            safe_history.append({"time": str(h.get("time", "")), "description": str(h.get("description", ""))})
                            
                try: adv_pay = float(item.get("advance_payment", 0))
                except: adv_pay = 0

                items_data.append({
                    "link": str(item.get("link", "")),
                    "carrier": str(item.get("carrier", "")),
                    "spx_code": str(item.get("spx_code", "")),
                    "spx_stage": current_stage,
                    "advance_payment": adv_pay,
                    "tracking_history": safe_history,
                    "current_lat": float(cur_coords["lat"]),
                    "current_lng": float(cur_coords["lng"]),
                    "receiver_lat": float(recv_coords["lat"]),
                    "receiver_lng": float(recv_coords["lng"]),
                })
                
            dt = o.get("created_at")
            if isinstance(dt, datetime): dt_str = dt.strftime("%d/%m/%Y %H:%M")
            else: dt_str = str(dt) if dt else datetime.now().strftime("%d/%m/%Y %H:%M")
            
            try: price_val = float(o.get("price", 0))
            except: price_val = 0
                
            result.append({
                "order_id": str(o.get("order_id", "")),
                "status": str(o.get("status", "Đang xử lý")),
                "product_name": str(o.get("product_name", "Đơn hàng mới")),
                "price": price_val,
                "created_at": dt_str,
                "receiver_name": str(o.get("receiver_name", "")),
                "phone": str(o.get("phone", "")),
                "address": receiver_address,
                "items": items_data
            })
        return result
    except Exception as e:
        print(f"ERROR: {e}")
        return JSONResponse(status_code=500, content={"detail": str(e)})


@app.get("/api/live_location")
def get_live_location(order_id: str = Query(...)):
    try:
        if not client: 
            return {"lat": 19.3833, "lng": 105.7833, "status": "Không có kết nối DB"}
        
        db = client['shop_database']
        order = db['orders'].find_one({"order_id": order_id})
        if not order: 
            return {"error": "Không tìm thấy đơn hàng"}
        
        items = order.get("items") or []
        db_address = str(order.get("address", "")).strip()
        current_stage = str(order.get("status", "")).strip()
        tracking_code = ""

        if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
            current_stage = str(items[0].get("spx_stage", current_stage)).strip()
            tracking_code = str(items[0].get("spx_code", "")).strip().upper()

        if tracking_code.startswith("SPX") or tracking_code.startswith("VN"):
            try:
                target_url = "https://dodanhvu.dpdns.org/api/spx"
                payload = {"trackings": [tracking_code]}
                headers = {"Content-Type": "application/json"}
                
                res = requests.post(target_url, json=payload, headers=headers, timeout=10)
                
                if res.status_code == 200:
                    data = res.json()
                    results = data.get("results", [])
                    
                    if results and len(results) > 0:
                        spx_data = results[0]
                        exact_location = ""
                        
                        latest = spx_data.get("latest", {})
                        records = spx_data.get("records", [])
                        
                        if latest.get("currentLoc"):
                            exact_location = latest.get("currentLoc")
                        elif records:
                            for rec in records: # API trả records mới nhất nằm đầu tiên
                                if rec.get("currentLoc"):
                                    exact_location = rec.get("currentLoc")
                                    break
                                    
                        final_status = exact_location if exact_location else (latest.get("desc") or spx_data.get("status"))
                        cur_coords = guess_coordinates(final_status)
                        
                        return {
                            "lat": cur_coords["lat"], 
                            "lng": cur_coords["lng"], 
                            "status": final_status, 
                            "source": "WEB_API"
                        }
            except Exception as e:
                print(f"Lỗi kết nối Web API cho mã {tracking_code}: {e}")

        location_text = db_address if db_address else current_stage
        if not location_text or location_text == "Đang xử lý":
            location_text = db_address

        cur_coords = guess_coordinates(location_text)
        
        return {
            "lat": cur_coords["lat"], 
            "lng": cur_coords["lng"], 
            "status": location_text, 
            "source": "DATABASE"
        }

    except Exception as e:
        print(f"Lỗi API live_location: {e}")
        return {"lat": 19.3833, "lng": 105.7833, "status": "Lỗi cập nhật"}


@app.get("/api/stats")
def get_web_stats(user_id: int = Query(0)):
    try:
        if not client: return {"online": 1, "monthly": 1}
        db = client['shop_database']
        now = datetime.utcnow()
        current_month = now.strftime("%Y-%m")
        if user_id != 0:
            db['web_stats'].update_one({"user_id": user_id, "month": current_month}, {"$set": {"last_active": now}}, upsert=True)
        three_mins_ago = now - timedelta(minutes=3)
        return {
            "online": max(1, db['web_stats'].count_documents({"last_active": {"$gte": three_mins_ago}})),
            "monthly": max(1, db['web_stats'].count_documents({"month": current_month}))
        }
    except:
        return {"online": 1, "monthly": 1}
