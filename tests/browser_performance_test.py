#!/usr/bin/env python3
"""Opt-in real Chromium benchmark of the embedded viewer (no engine quota).

Runs the production console, websocket and managed browser on loopback. A
dashboard and a long article exercise animation, typing, pointer movement and
scrolling. Pixel markers measure the age of images actually decoded by the
viewer, rather than counting websocket arrivals. Results are observations,
not machine-dependent pass/fail latency thresholds.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.console_browser_test import (
    ROOT, browser, config, evaluate, fixture, engines, open_console, until, webui)
from aiohttp import web


PAGE = """<!doctype html><meta charset=utf-8><title>Browser performance fixture</title>
<style>
body{margin:0;background:#182030;color:#edf2ff;font:16px system-ui}
main{max-width:1000px;margin:auto;padding:50px 24px}
input{font:inherit;padding:12px;width:80%}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:24px 0}
.card{height:130px;padding:16px;border-radius:12px;background:#28364f;overflow:hidden}
.bar{height:12px;background:#72ddc7;margin-top:22px;transform-origin:left;
 animation:pulse 1.6s ease-in-out infinite alternate}
@keyframes pulse{from{transform:scaleX(.1)}to{transform:scaleX(1)}}
p{line-height:1.8} #marker{position:fixed;left:0;top:0;z-index:100;width:256px;height:16px}
</style><canvas id=marker width=256 height=16></canvas><main>
<h1>Harbor activity dashboard</h1><input aria-label="Filter activity" autofocus>
<div class=grid></div><article></article></main><script>
document.querySelector('.grid').innerHTML=Array.from({length:24},(_,i)=>
 '<div class=card>Project '+(i+1)+'<div class=bar style="animation-delay:-'+i/9+'s"></div></div>').join('');
document.querySelector('article').innerHTML=Array.from({length:80},(_,i)=>
 '<h2>Section '+(i+1)+'</h2><p>'+('A practical guide to arranging a workspace, reviewing changes, and keeping track of activity. '.repeat(8))+'</p>').join('');
let keys=0,wheels=0,point=0;
addEventListener('keydown',()=>keys=(keys+1)%256);
addEventListener('wheel',()=>wheels=(wheels+1)%256,{passive:true});
addEventListener('mousemove',e=>point=Math.round(e.clientX));
const ctx=document.querySelector('#marker').getContext('2d',{alpha:false});
function paint(){
 const values=[[Date.now()%16777216,24],[keys,8],[wheels,8],[point,12],[Math.round(scrollY)%4096,12]];
 let bit=0;
 for(const [value,width] of values) for(let j=0;j<width;j++,bit++){
   ctx.fillStyle=(value>>j)&1?'#fff':'#000';ctx.fillRect(bit*4,0,4,16);
 }
 requestAnimationFrame(paint);
} paint();
</script>"""

SAMPLER = """(() => {
 const v=window.perfView;
 const canvas=document.createElement('canvas');canvas.width=256;canvas.height=16;
 const ctx=canvas.getContext('2d',{willReadFrequently:true});
 window.samples=[];window.inputs={key:[],wheel:[],move:[]};window.sampling=true;
 window.invalidSamples=0;
 const now=()=>Date.now();
 v.stage.addEventListener('keydown',()=>inputs.key.push(now()),true);
 v.stage.addEventListener('wheel',()=>inputs.wheel.push(now()),true);
 v.stage.addEventListener('mousemove',e=>{
   const p=v.point(e.clientX,e.clientY);
   if(p) inputs.move.push([Math.round(p.nx*v.frameW),now()]);
 },true);
 let last=-1;
 function sample(){
   if(!sampling)return;
   try{
     ctx.drawImage(v.screen,0,0,256,16,0,0,256,16);
     const pixels=ctx.getImageData(0,0,256,16).data;
     let bit=0;
     const values=[24,8,8,12,12].map(width=>{
       let value=0;
       for(let j=0;j<width;j++,bit++)if(pixels[(8*256+bit*4+2)*4]>128)value+=2**j;
       return value;
     });
     if((now()%16777216-values[0]+16777216)%16777216>1000)invalidSamples++;
     else if(values[0]!==last){samples.push([now(),...values]);last=values[0];}
   }catch(e){}
   requestAnimationFrame(sample);
 } requestAnimationFrame(sample);
 return true;
})()"""


def stats(values):
    values = sorted(values)
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None}
    return {"n": len(values), "p50_ms": round(values[len(values)//2], 1),
            "p95_ms": round(values[min(len(values)-1, int(len(values)*.95))], 1)}


def summarize(raw, flow, elapsed):
    samples, inputs = raw["samples"], raw["inputs"]
    assert raw["invalidSamples"] == 0, "unreadable pixel markers during measurement"
    ages = [(row[0] % 16777216 - row[1]) % 16777216 for row in samples]
    assert len(samples) > 30 and max(ages) < 10000, "pixel markers were not readable"
    intervals = [b[0]-a[0] for a, b in zip(samples, samples[1:])]
    result = {"display_fps": round((len(samples)-1)*1000/(samples[-1][0]-samples[0][0]), 2),
              "frame_age": stats(ages), "frame_interval": stats(intervals),
              "gaps_over_50ms": sum(x > 50 for x in intervals),
              "frame_flow": flow, "duration_seconds": round(elapsed, 2)}
    for kind, column in (("key", 2), ("wheel", 3)):
        latencies = []
        for i, sent in enumerate(inputs[kind], 1):
            seen = next((row[0] for row in samples if row[0] >= sent and row[column] >= i), None)
            if seen is not None:
                latencies.append(seen-sent)
        assert len(latencies) == len(inputs[kind]), "unacknowledged " + kind + " input"
        result[kind+"_to_image"] = stats(latencies)
        result[kind+"_sent"] = len(inputs[kind])
    latencies = []
    for point, sent in inputs["move"]:
        seen = next((row[0] for row in samples if row[0] >= sent and row[4] == point), None)
        if seen is not None:
            latencies.append(seen-sent)
    result["pointer_to_image"] = stats(latencies)
    result["pointer_sent"] = len(inputs["move"])
    result["scrolled_pixels"] = max(row[5] for row in samples)
    latencies = []
    for i, sent in enumerate(inputs["wheel"]):
        seen = next((row[0] for row in samples if row[0] >= sent and row[5] > i*48), None)
        if seen is not None:
            latencies.append(seen-sent)
    result["wheel_to_scroll"] = stats(latencies)
    return result


async def run_case(console, url, width, height, scenario, seconds, external_url=None):
    await console.call("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        session=console.page_session)
    page = await browser.manager().create()
    try:
        await evaluate(console, "openBrowserTab(0,%s); window.perfView=Object.values(state.views).find(v=>v instanceof BrowserView);true" % json.dumps(page.browser_id))
        await until(console, "perfView.screen.classList.contains('live') && perfView.viewerActive")
        await page.call("Page.navigate", {"url": external_url if scenario == "reference" else url+"performance-page"}, session=page.page_session)
        if scenario == "reference":
            await until(page, "document.readyState === 'complete' && document.documentURI.startsWith('http') && document.body.innerText.length > 1000")
            probe = PAGE[PAGE.index("let keys="):PAGE.index("</script>")]
            await evaluate(page, """(() => {
              const marker=document.createElement('canvas');marker.id='marker';
              marker.width=256;marker.height=16;
              marker.style.cssText='position:fixed;left:0;top:0;width:256px;height:16px;z-index:2147483647;pointer-events:none';
              document.body.appendChild(marker);
            """ + probe + "return true;})()")
        else:
            await until(page, "document.title === 'Browser performance fixture'")
        if scenario == "article":
            await evaluate(page, "document.querySelector('.grid').remove();true")
        await asyncio.sleep(.7)
        await evaluate(console, SAMPLER)
        await until(console, "samples.length >= 10")
        await evaluate(console, "samples=[];invalidSamples=0;true")
        await evaluate(console, "perfView.stage.focus();true")
        page._reset_frame_flow()
        started = time.monotonic()
        # Native outer-browser events go through BrowserView's listeners and
        # the authenticated websocket to the separately running page.
        point = await evaluate(console, "(() => {const r=perfView.screen.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
        for i in range(int(seconds/.16)):
            if scenario == "dashboard":
                await console.call("Input.dispatchKeyEvent", {"type":"keyDown", "key":"a", "code":"KeyA", "text":"a"}, session=console.page_session)
                await console.call("Input.dispatchKeyEvent", {"type":"keyUp", "key":"a", "code":"KeyA"}, session=console.page_session)
            await console.call("Input.dispatchMouseEvent", {"type":"mouseMoved", "x":point["x"]+30+(i*7) % max(40,int(point["w"])-60), "y":point["y"]+point["h"]*.6}, session=console.page_session)
            if scenario != "dashboard":
                await console.call("Input.dispatchMouseEvent", {"type":"mouseWheel", "x":point["x"]+point["w"]*.5, "y":point["y"]+point["h"]*.6, "deltaX":0,"deltaY":48}, session=console.page_session)
            await asyncio.sleep(.16)
        await asyncio.sleep(.3)
        raw = await evaluate(console, "sampling=false;({samples,inputs,invalidSamples})")
        try:
            result = summarize(raw, page.frame_flow_payload(), time.monotonic()-started)
        except AssertionError:
            (BASE/"data/browser-performance-failure.json").write_text(json.dumps(raw))
            raise
        result.update({"scenario": scenario, "console": [width,height], "viewport": page.viewport})
        result["raw"] = raw
        return result
    finally:
        await evaluate(console, "perfView.onVisibility(false);true")
        for attempt in range(100):
            if not page._has_active_viewers():
                break
            await asyncio.sleep(.02)
        await page._stop_screencast_if_idle()
        await evaluate(console, "closeTab(perfView.tab.id);true")
        await browser.manager().close(page.browser_id, "Performance test finished")


async def main(args):
    console, server, page_server = None, None, None
    results = []
    try:
        with patch.object(webui, "_engines_payload", engines), patch.object(webui, "_node_user", return_value="mira"):
            app, sid = await fixture()
            async def serve_page(request):
                return web.Response(text=PAGE, content_type="text/html")
            page_app = web.Application()
            page_app.router.add_get("/performance-page", serve_page)
            page_server = web.AppRunner(page_app)
            await page_server.setup()
            page_site = web.TCPSite(page_server, "127.0.0.1", 0)
            await page_site.start()
            page_url = "http://127.0.0.1:{}/".format(page_site._server.sockets[0].getsockname()[1])
            server = web.AppRunner(app)
            await server.setup()
            site = web.TCPSite(server, "127.0.0.1", 0)
            await site.start()
            url = "http://127.0.0.1:{}/".format(site._server.sockets[0].getsockname()[1])
            config.set_value("browser.enabled", True)
            browser.manager()
            console = browser.Manager("PERF")
            await open_console(console, url, sid)
            sources = {}
            paths = [(args.label, args.viewer_source or BASE/"puppy/static/app.js")]
            if args.compare_source:
                paths.insert(0, ("before", args.compare_source))
            for label, path in paths:
                source = path.read_text()
                start = source.index("class BrowserView {")
                end = source.index("\n/* ================= ", start)
                sources[label] = source[start:end]
            output = {"label":args.label,"external_url":args.external_url,
                      "viewer_sha256":{k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()},
                      "browser":await console.call("Browser.getVersion"),"results":results}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            for repeat in range(args.repeats):
                sizes = {"desktop":(1440,900), "phone":(390,844)}
                for width,height in ([sizes[args.viewport]] if args.viewport != "both" else sizes.values()):
                    scenarios = ["reference"] if args.external_url else (
                        [args.scenario] if args.scenario != "all" else ["dashboard", "article"])
                    for scenario in scenarios:
                        order = list(sources.items())
                        if repeat % 2:
                            order.reverse()
                        for label, source in order:
                            await evaluate(console, "BrowserView = " + source + ";true")
                            result = await run_case(console,page_url,width,height,scenario,args.seconds,args.external_url)
                            result.update({"repeat":repeat+1,"variant":label})
                            results.append(result)
                            print(json.dumps({k:v for k,v in result.items() if k != "raw"}), flush=True)
                            args.output.write_text(json.dumps(output,indent=2)+"\n")
    finally:
        if console:
            await console.stop("Performance test finished")
        await browser.shutdown()
        if server:
            await server.cleanup()
        if page_server:
            await page_server.cleanup()
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="current")
    parser.add_argument("--viewer-source", type=Path, help="Compare a saved BrowserView on the same backend")
    parser.add_argument("--compare-source", type=Path, help="Alternate this saved baseline with the current viewer")
    parser.add_argument("--external-url", help="Opt-in public reference page to scroll instead of the local workloads")
    parser.add_argument("--scenario", choices=("all", "dashboard", "article"), default="all")
    parser.add_argument("--viewport", choices=("both", "desktop", "phone"), default="both")
    parser.add_argument("--output", type=Path, default=BASE/"data"/"browser-performance.json")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=6)
    args = parser.parse_args()
    if args.repeats < 1 or not 1 <= args.seconds <= 12:
        parser.error("use positive repeats and 1–12 seconds (bounded pixel counters)")
    if args.compare_source and (args.viewer_source or args.label == "before"):
        parser.error("--compare-source needs a distinct current label and no --viewer-source")
    asyncio.run(main(args))
