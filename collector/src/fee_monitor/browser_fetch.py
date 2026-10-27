"""Optional Chromium rendering using the project's existing Playwright runtime."""
import json,os,shutil,subprocess,sys,threading
from pathlib import Path
from .store import ROOT


class BrowserFetcher:
    def __init__(self,ua='FeeDisclosureResearch/1.2 (user-requested public disclosure review)'):self.process=None;self.ua=ua

    def get(self,url,**options):
        from .collector import validate_url
        validate_url(url,True)
        if self.process is None:
            node=os.environ.get('FEES_NODE') or shutil.which('node')
            bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            if not node and bundled.exists():node=str(bundled)
            if not node:raise ValueError('未找到浏览器运行时，需人工核验动态页面')
            self.process=subprocess.Popen([node,str(ROOT/'scripts/browser_disclosure.cjs')],cwd=ROOT,env={**os.environ,'FEES_CRAWLER_UA':self.ua},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        self.process.stdin.write(json.dumps({'url':url,'python':sys.executable,**options})+'\n');self.process.stdin.flush()
        lines=[]
        reader=threading.Thread(target=lambda:lines.append(self.process.stdout.readline()),daemon=True)
        reader.start();reader.join(60)
        if reader.is_alive():
            self.process.kill();self.process.wait(timeout=5);self.process=None
            raise ValueError('浏览器复核超过60秒，已终止子进程')
        line=lines[0] if lines else ''
        if not line:raise ValueError('浏览器运行失败：'+self.process.stderr.read()[:500])
        result=json.loads(line)
        if result.get('error'):raise ValueError(result['error'])
        validate_url(result['final_url'],True)
        return result

    def close(self):
        if self.process:
            try:self.process.stdin.close()
            except (BrokenPipeError,OSError):pass
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:self.process.terminate()
            self.process=None
