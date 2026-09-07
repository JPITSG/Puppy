#!/usr/bin/env python3
"""Opt-in comparison with two real console profiles watching one browser.

Both viewers receive identical source frames. Shared input timestamps and
matching pixel markers separate viewer delay from source-capture timing.
Uses the browser performance harness; external pages are explicitly opt-in.
"""
import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests import browser_performance_test as b


async def main(args):
    consoles = []
    server = None
    results = []
    try:
        with patch.object(b.webui, '_engines_payload', b.engines), patch.object(b.webui, '_node_user', return_value='mira'):
            (app, sid) = await b.fixture()
            server = b.web.AppRunner(app)
            await server.setup()
            site = b.web.TCPSite(server, '127.0.0.1', 0)
            await site.start()
            url = 'http://127.0.0.1:{}/'.format(site._server.sockets[0].getsockname()[1])
            b.config.set_value('browser.enabled', True)
            b.browser.manager()
            sources = [args.baseline, BASE / 'puppy/static/app.js']
            hashes = {}
            for (i, path) in enumerate(sources):
                c = b.browser.Manager('SHR' + str(i))
                consoles.append(c)
                await b.open_console(c, url, sid)
                source = path.read_text()
                start = source.index('class BrowserView {')
                end = source.index('\n/* ================= ', start)
                await b.evaluate(c, 'BrowserView = ' + source[start:end] + ';true')
                hashes[('before', 'after')[i]] = hashlib.sha256(source[start:end].encode()).hexdigest()
            output = {'browser': await consoles[0].call('Browser.getVersion'), 'external_url': args.external_url, 'viewer_sha256': hashes, 'results': results}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            for repeat in range(args.repeats):
                for (width, height) in ((1440, 900), (390, 844)):
                    for scenario in ('reference',) if args.external_url else ('dashboard', 'article'):
                        page = await b.browser.manager().create()
                        try:
                            for c in consoles:
                                await c.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': False}, session=c.page_session)
                                await b.evaluate(c, 'openBrowserTab(0,%s);window.perfView=Object.values(state.views).find(v=>v instanceof BrowserView);true' % json.dumps(page.browser_id))
                                await b.until(c, "perfView.screen.classList.contains('live') && perfView.viewerActive")
                            if scenario == 'reference':
                                await page.call('Page.navigate', {'url': args.external_url}, session=page.page_session)
                                await b.until(page, "document.readyState==='complete' && document.documentURI.startsWith('http') && document.body.innerText.length>1000")
                                probe = b.PAGE[b.PAGE.index('let keys='):b.PAGE.index('</script>')]
                                await b.evaluate(page, "(()=>{const m=document.createElement('canvas');m.id='marker';m.width=256;m.height=16;m.style.cssText='position:fixed;left:0;top:0;width:256px;height:16px;z-index:2147483647;pointer-events:none';document.body.appendChild(m);" + probe + 'return true;})()')
                            else:
                                tree = await page.call('Page.getFrameTree', session=page.page_session)
                                await page.call('Page.setDocumentContent', {'frameId': tree['frameTree']['frame']['id'], 'html': b.PAGE}, session=page.page_session)
                                await b.until(page, "document.title==='Browser performance fixture'")
                                if scenario == 'article':
                                    await b.evaluate(page, "document.querySelector('.grid').remove();true")
                            await asyncio.sleep(0.7)
                            for c in consoles:
                                await b.evaluate(c, b.SAMPLER)
                                await b.until(c, 'samples.length>=10')
                            await asyncio.gather(*(b.evaluate(c, 'samples=[];invalidSamples=0;true') for c in consoles))
                            sender = consoles[repeat % 2]
                            await b.evaluate(sender, 'perfView.stage.focus();true')
                            point = await b.evaluate(sender, '(()=>{const r=perfView.screen.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}})()')
                            page._reset_frame_flow()
                            started = time.monotonic()
                            for i in range(int(args.seconds / 0.16)):
                                if scenario == 'dashboard':
                                    await sender.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'a', 'code': 'KeyA', 'text': 'a'}, session=sender.page_session)
                                    await sender.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'a', 'code': 'KeyA'}, session=sender.page_session)
                                await sender.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': point['x'] + 30 + i * 7 % max(40, int(point['w']) - 60), 'y': point['y'] + point['h'] * 0.6}, session=sender.page_session)
                                if scenario != 'dashboard':
                                    await sender.call('Input.dispatchMouseEvent', {'type': 'mouseWheel', 'x': point['x'] + point['w'] * 0.5, 'y': point['y'] + point['h'] * 0.6, 'deltaX': 0, 'deltaY': 48}, session=sender.page_session)
                                await asyncio.sleep(0.16)
                            await asyncio.sleep(0.3)
                            raw = await asyncio.gather(*(b.evaluate(c, 'sampling=false;({samples,inputs,invalidSamples})') for c in consoles))
                            inputs = raw[repeat % 2]['inputs']
                            row = {'repeat': repeat + 1, 'scenario': scenario, 'console': [width, height], 'input_viewer': ['before', 'after'][repeat % 2]}
                            for (i, variant) in enumerate(('before', 'after')):
                                raw[i]['inputs'] = inputs
                                row[variant] = b.summarize(raw[i], page.frame_flow_payload(), time.monotonic() - started)
                            old = {r[1]: r[0] for r in raw[0]['samples']}
                            new = {r[1]: r[0] for r in raw[1]['samples']}
                            row['matched_frame_delay_after_minus_before'] = b.stats([new[k] - old[k] for k in old.keys() & new.keys()])
                            print(json.dumps(row), flush=True)
                            row['raw'] = raw
                            results.append(row)
                            args.output.write_text(json.dumps(output, indent=2) + '\n')
                        finally:
                            for c in consoles:
                                await b.evaluate(c, 'closeTab(perfView.tab.id);true')
                            await b.browser.manager().close(page.browser_id, 'Shared test finished')
    finally:
        for c in consoles:
            await c.stop('Shared test finished')
        await b.browser.shutdown()
        if server:
            await server.cleanup()
        shutil.rmtree(b.ROOT)
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='Saved original app.js')
    parser.add_argument('--output', type=Path, default=BASE / 'data/browser-performance/shared.json')
    parser.add_argument('--external-url', help='Public page to scroll instead of local workloads')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--seconds', type=float, default=6)
    args = parser.parse_args()
    if args.repeats < 1 or not 1 <= args.seconds <= 12:
        parser.error('use positive repeats and 1–12 seconds (bounded pixel counters)')
    asyncio.run(main(args))
