from http.server import BaseHTTPRequestHandler
import json
import math
import sqlite3

def respond(h,status,data):
    body=json.dumps(data).encode()
    h.send_response(status); h.send_header("Content-Type","application/json; charset=utf-8")
    h.send_header("Cache-Control","no-store"); h.send_header("Content-Length",str(len(body))); h.end_headers(); h.wfile.write(body)

def analyze(spans, threshold):
    if not isinstance(spans,list) or len(spans)>500: raise ValueError("spans must be an array of at most 500 items")
    by_id={}
    for raw in spans:
        sid=str(raw.get("spanId",""))
        if not sid or sid in by_id: raise ValueError("Every span needs a unique spanId")
        start=float(raw.get("startOffsetMs",0)); dur=float(raw.get("durationMs",0))
        if not math.isfinite(start) or not math.isfinite(dur) or start<0 or dur<0: raise ValueError("Span timings must be finite and non-negative")
        by_id[sid]={**raw,"spanId":sid,"startOffsetMs":start,"durationMs":dur,"endOffsetMs":start+dur}
    children={sid:[] for sid in by_id}
    for sid,s in by_id.items():
        parent=s.get("parentSpanId")
        if parent and parent in by_id: children[parent].append(sid)
    # Detect cycles before recursive depth calculations.
    visiting=set(); visited=set()
    def visit(sid):
        if sid in visiting: raise ValueError("Parent relationships contain a cycle")
        if sid in visited: return
        visiting.add(sid)
        for child in children[sid]: visit(child)
        visiting.remove(sid); visited.add(sid)
    for sid in by_id: visit(sid)
    total=max((s["endOffsetMs"] for s in by_id.values()),default=0)
    for sid,s in by_id.items():
        child_intervals=sorted((by_id[c]["startOffsetMs"],by_id[c]["endOffsetMs"]) for c in children[sid])
        covered=0; left=right=None
        for a,b in child_intervals:
            a=max(a,s["startOffsetMs"]); b=min(b,s["endOffsetMs"])
            if b<=a: continue
            if left is None: left,right=a,b
            elif a<=right: right=max(right,b)
            else: covered+=right-left; left,right=a,b
        if left is not None: covered+=right-left
        s["inclusiveDurationMs"]=round(s["durationMs"],2)
        s["selfTimeMs"]=round(max(0,s["durationMs"]-covered),2)
        s["shareOfTracePct"]=round((s["durationMs"]/total*100) if total else 0,1)
        s["isCriticalPath"]=False
        s["flags"]=[]
    # Critical path: longest accumulated duration through the parent-child tree.
    memo={}
    def path(sid):
        if sid in memo: return memo[sid]
        best=max((path(c) for c in children[sid]),key=lambda x:x[0],default=(0,[]))
        memo[sid]=(by_id[sid]["selfTimeMs"]+best[0],[sid]+best[1])
        return memo[sid]
    roots=[sid for sid,s in by_id.items() if not s.get("parentSpanId") or s.get("parentSpanId") not in by_id]
    best=max((path(r) for r in roots),key=lambda x:x[0],default=(0,[]))
    for sid in best[1]: by_id[sid]["isCriticalPath"]=True
    findings=[]
    for sid,s in by_id.items():
        flags=[]
        if str(s.get("status","OK")).upper() in ("ERROR","FAILED"): flags.append("ERROR_SPAN")
        if s["isCriticalPath"] and s["durationMs"]>=threshold: flags.append("CRITICAL_BOTTLENECK")
        if s["durationMs"]>=threshold and "CRITICAL_BOTTLENECK" not in flags: flags.append("SLOW_SPAN")
        siblings=[by_id[c] for c in children.get(sid,[])]
        # repeated operations among siblings, and repeated sequential operations under a common parent
        parent=s.get("parentSpanId")
        peer=[x for x in by_id.values() if x.get("parentSpanId")==parent and x["spanId"]!=sid] if parent else []
        same=[x for x in peer if x.get("service")==s.get("service") and x.get("operation")==s.get("operation")]
        if len(same)>=1: flags.append("SEQUENTIAL_REPEAT_PATTERN")
        if not flags: flags.append("HEALTHY")
        s["flags"]=flags
        if any(f in flags for f in ("CRITICAL_BOTTLENECK","ERROR_SPAN","SEQUENTIAL_REPEAT_PATTERN","SLOW_SPAN")):
            advice=("Inspect downstream latency and apply bounded timeouts/circuit breaking." if "CRITICAL_BOTTLENECK" in flags else
                    "Review retries, error handling, and dependency availability." if "ERROR_SPAN" in flags else
                    "Batch repeated calls or run independent work concurrently." if "SEQUENTIAL_REPEAT_PATTERN" in flags else
                    "Profile the operation and consider caching or query/index optimization.")
            findings.append({"spanId":sid,"service":s.get("service"),"operation":s.get("operation"),"durationMs":s["durationMs"],"selfTimeMs":s["selfTimeMs"],"flags":flags,"recommendation":advice,"severity":"high" if "ERROR_SPAN" in flags or "CRITICAL_BOTTLENECK" in flags else "medium"})
    enriched=sorted(by_id.values(),key=lambda x:(x["startOffsetMs"],x["spanId"]))
    findings.sort(key=lambda x:(0 if x["severity"]=="high" else 1,-x["selfTimeMs"]))
    return {"traceId":None,"totalDurationMs":round(total,2),"criticalPathSelfTimeMs":round(best[0],2),"spans":enriched,"findings":findings,"summary":{"spanCount":len(enriched),"errorCount":sum("ERROR_SPAN" in s["flags"] for s in enriched),"criticalCount":sum(s["isCriticalPath"] for s in enriched)}}

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.split("?")[0]!="/api/analyze": return respond(self,404,{"error":"Not found"})
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length>1_000_000: return respond(self,413,{"error":"Payload too large"})
            payload=json.loads(self.rfile.read(length) or b"{}")
            spans=payload.get("spans")
            if spans is None:
                db=sqlite3.connect(":memory:")
                return respond(self,400,{"error":"Provide spans in the request payload"})
            threshold=max(1,min(60000,float(payload.get("slowSpanThresholdMs",250))))
            result=analyze(spans,threshold); result["traceId"]=payload.get("traceId")
            respond(self,200,result)
        except (ValueError,TypeError,KeyError) as exc: respond(self,400,{"error":str(exc)})
    def log_message(self,*_): pass
