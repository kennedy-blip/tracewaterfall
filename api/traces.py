from http.server import BaseHTTPRequestHandler
import json
import sqlite3
import uuid
from datetime import datetime, timezone

def connect():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE traces (trace_id TEXT PRIMARY KEY, name TEXT NOT NULL, service TEXT NOT NULL, started_at TEXT NOT NULL);
      CREATE TABLE spans (span_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, parent_span_id TEXT, service TEXT NOT NULL, operation TEXT NOT NULL, start_offset_ms REAL NOT NULL, duration_ms REAL NOT NULL, status TEXT NOT NULL);
    """)
    seed(db)
    return db

def seed(db):
    now = datetime.now(timezone.utc).isoformat()
    traces = [
      ("checkout-stk-push","Checkout STK Push","checkout-api",now),
      ("catalog-feed-sync","Catalog Feed Sync","catalog-worker",now),
      ("healthy-profile-read","Healthy Profile Read","profile-api",now)
    ]
    db.executemany("INSERT INTO traces VALUES (?,?,?,?)", traces)
    spans = [
      ("c1","checkout-stk-push",None,"checkout-api","POST /checkout",0,1120,"OK"),
      ("c2","checkout-stk-push","c1","payment-service","create payment intent",18,1050,"OK"),
      ("c3","checkout-stk-push","c2","mpesa-gateway","STK push request",40,720,"OK"),
      ("c4","checkout-stk-push","c2","orders-db","insert order (lock wait)",790,245,"OK"),
      ("c5","checkout-stk-push","c1","risk-engine","score transaction",90,130,"OK"),
      ("c6","checkout-stk-push","c1","notification","publish receipt",960,110,"ERROR"),
      ("f1","catalog-feed-sync",None,"catalog-worker","sync catalog",0,980,"OK"),
      ("f2","catalog-feed-sync","f1","supplier-api","fetch page 1",20,260,"OK"),
      ("f3","catalog-feed-sync","f1","supplier-api","fetch page 2",290,255,"OK"),
      ("f4","catalog-feed-sync","f1","supplier-api","fetch page 3",560,270,"OK"),
      ("f5","catalog-feed-sync","f1","catalog-db","upsert products",840,120,"OK"),
      ("p1","healthy-profile-read",None,"profile-api","GET /profile",0,86,"OK"),
      ("p2","healthy-profile-read","p1","cache","get profile",5,8,"OK"),
      ("p3","healthy-profile-read","p1","users-db","select profile",18,42,"OK")
    ]
    db.executemany("INSERT INTO spans VALUES (?,?,?,?,?,?,?,?)", spans)
    db.commit()

def response(handler, status, payload):
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type","application/json; charset=utf-8")
    handler.send_header("Cache-Control","no-store")
    handler.send_header("Content-Length",str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] != "/api/traces":
            return response(self,404,{"error":"Not found"})
        db = connect()
        traces = []
        for row in db.execute("SELECT * FROM traces ORDER BY started_at DESC"):
            spans = [dict(x) for x in db.execute("SELECT span_id AS spanId,parent_span_id AS parentSpanId,service,operation,start_offset_ms AS startOffsetMs,duration_ms AS durationMs,status FROM spans WHERE trace_id=? ORDER BY start_offset_ms",(row["trace_id"],))]
            total = max((s["startOffsetMs"]+s["durationMs"] for s in spans), default=0)
            traces.append({"traceId":row["trace_id"],"name":row["name"],"service":row["service"],"startedAt":row["started_at"],"totalDurationMs":round(total,2),"spanCount":len(spans),"errorCount":sum(s["status"]=="ERROR" for s in spans),"spans":spans})
        response(self,200,{"traces":traces,"engine":"Python 3.12 + SQLite","seeded":True})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/traces":
            return response(self,404,{"error":"Not found"})
        try:
            length = int(self.headers.get("Content-Length","0"))
            if length > 1_000_000: return response(self,413,{"error":"Payload too large"})
            payload = json.loads(self.rfile.read(length) or b"{}")
            db = connect()
            if payload.get("reset"):
                return response(self,200,{"ok":True,"message":"Demo traces restored"})
            name = str(payload.get("name","Custom trace"))[:120]
            trace_id = str(payload.get("traceId") or "custom-"+uuid.uuid4().hex[:10])
            spans = payload.get("spans",[])
            if not isinstance(spans,list) or len(spans)>500: return response(self,400,{"error":"spans must be an array of at most 500 items"})
            db.execute("INSERT INTO traces VALUES (?,?,?,?)",(trace_id,name,str(payload.get("service","custom-service"))[:100],datetime.now(timezone.utc).isoformat()))
            seen=set()
            for s in spans:
                sid=str(s.get("spanId") or uuid.uuid4().hex[:12])
                if sid in seen: return response(self,400,{"error":"spanId values must be unique"})
                seen.add(sid)
                start=float(s.get("startOffsetMs",0)); duration=float(s.get("durationMs",0))
                if start<0 or duration<0 or start>3_600_000 or duration>3_600_000: return response(self,400,{"error":"span timing must be between 0 and 3600000ms"})
                db.execute("INSERT INTO spans VALUES (?,?,?,?,?,?,?,?)",(sid,trace_id,s.get("parentSpanId"),str(s.get("service","service"))[:100],str(s.get("operation","operation"))[:160],start,duration,str(s.get("status","OK")).upper()))
            db.commit()
            return response(self,201,{"ok":True,"traceId":trace_id,"spanCount":len(spans)})
        except (ValueError,TypeError,KeyError,sqlite3.Error) as exc:
            return response(self,400,{"error":str(exc)})
    def log_message(self, *_): pass
