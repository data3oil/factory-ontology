# -*- coding: utf-8 -*-
"""推送前合规扫描：查脱敏与密钥问题。

两种模式：
    工作区（默认）   扫 git 跟踪 + 未跟踪的文件，也就是"这次要提交的东西"
    --history        扫全部历史 blob，查"曾经提交过、后来删了"的遗留
    --all            两者都扫

用法:
    python scripts/pre_push_scan.py
    python scripts/pre_push_scan.py --history            # 只报告，不判失败
    python scripts/pre_push_scan.py --history --strict   # 历史有遗留也判失败（清理时用）

退出码：工作区有未豁免命中 = 1；历史遗留默认不判失败，加 --strict 才判。
豁免见 scripts/pre_push_scan_allow.json，每条必须写理由，便于以后复核。

⚠ 两个曾把人骗过去的地方，改这里之前先读：
1. `git ls-files` 默认 core.quotepath=true，中文文件名会输出成
   "docs/\\345\\256\\236\\351\\252\\214/..." 这种转义形式。拿它当路径去
   os.path.isfile() 永远返回假，于是所有中文名文件被静默跳过——
   2026-11-10 核查时发现 30 处本机路径全在中文名文档里，一处都没报出来。
   所以这里统一用 -z 取原始字节再按 utf-8 解码。
2. 只扫工作区看不出历史。删掉的文件、改过的行都还在 git 历史里，
   公开仓库 clone 一次就全拿到了。要判断"到底泄露过什么"必须用 --history。
"""
import fnmatch
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 仓库根，不写死本机路径

# 规则名要与 allow 文件里的 rule 字段一致
PATTERNS = [
    # 盘符 + 真实本地目录 / 本机解释器安装目录；
    # 前面不能是字母数字或 :/ ，否则会把 http://factory-ontology 当成路径
    ("本机绝对路径", re.compile(
        r"(?<![A-Za-z0-9:/])[A-Za-z]:[\\/]+(?:Users|workspace|Desktop|factory-ontology|knowledge"
        r"|Python\d{2,3}(?![0-9])|data(?![a-z.]))",
        re.I)),
    ("用户名", re.compile(r"(?i)\bzjjem\b|C:[\\/]+Users[\\/]+[A-Za-z0-9._-]+")),
    ("内网IP", re.compile(
        r"(?<![\d.])(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?![\d.])")),
    ("内部项目名", re.compile(r"获客飞轮|组织主体舆情|企业人才系统|企业内训|客讯引擎")),
    ("疑似密钥", re.compile(
        r"(?i)sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}"
        r"|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|xox[baprs]-[A-Za-z0-9-]{10,}")),
    # 密码位是 *** 的是文档里的示例，不是真串
    ("带密码连接串", re.compile(
        r"(?i)\b(?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis)://[^\s\"'<>]+:(?!\*+@)[^\s\"'<>@]+@")),
    # 变量名里带 password/secret 的赋字面量。刻意不匹配 *KEY：前端大量
    # TOKEN_KEY = 'factory_enterprise_token' 这类存储键名，会把规则变成噪音源。
    ("硬编码口令", re.compile(
        r"(?i)\b[A-Za-z0-9_]*(?:password|passwd|secret)[A-Za-z0-9_]*\s*=\s*['\"][^'\"]{6,}['\"]")),
    # 前面不能是数字或 /：知乎链接 zhuanlan.zhihu.com/p/16792939911 不是手机号
    ("手机号", re.compile(r"(?<![\d/])1[3-9]\d{9}(?!\d)|(?<![\d/])1[3-9]\d[- ]\d{4}[- ]\d{4}(?!\d)")),
    ("真实邮箱", re.compile(
        r"[\w.+-]+@(?!example\.|test\.|localhost|users\.noreply)[\w-]+\.[a-z]{2,}")),
]

SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".zip", ".whl",
            ".db", ".sqlite3", ".nt", ".pdf", ".xlsx", ".docx", ".svg", ".mp4", ".wasm"}
MAX_BYTES = 3_000_000          # 超过 3MB 的文本不当源码扫；要扫大文件先确认它不是数据导出
SELF = os.path.join("scripts", "pre_push_scan.py")
ALLOW_FILE = os.path.join(ROOT, "scripts", "pre_push_scan_allow.json")


def sh(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True).stdout


def decode(b):
    return b.decode("utf-8", "replace")


def tracked_files():
    """用 -z 拿原始路径，避免中文名被 quotepath 转义。"""
    out = sh("git", "ls-files", "-z")
    files = [p for p in decode(out).split("\x00") if p]
    out = sh("git", "ls-files", "--others", "--exclude-standard", "-z")
    files += [p for p in decode(out).split("\x00") if p]
    return files


def load_allow():
    if not os.path.exists(ALLOW_FILE):
        return []
    try:
        doc = json.load(open(ALLOW_FILE, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("！豁免清单读不了（%s），按空清单处理" % e)
        return []
    return [x for x in (doc.get("entries") or []) if x.get("path") and x.get("rule")]


def is_allowed(path, rule, allow):
    for a in allow:
        if a["rule"] == rule and fnmatch.fnmatch(path, a["path"]):
            return True
    return False


def plausible(rule, matched):
    """正则粗筛之后的语义复核，专治"长得像但不是"的命中。"""
    if rule == "带密码连接串":
        # mysql://u:***@h/p 里的 *** 是文档占位符，不是真密码
        try:
            pwd = matched.split("://", 1)[1].rsplit("@", 1)[0].split(":")[-1]
        except IndexError:
            return True
        return set(pwd.lower()) - set("*x") != set()
    if rule == "硬编码口令":
        # 取等号后面的引号内容：含空格说明是拼接/说明文字，不是字面量凭据
        try:
            val = re.split(r"=\s*['\"]", matched, 1)[1].rstrip("'\"")
        except IndexError:
            return True
        return bool(val) and not re.search(r"\s", val)
    return True


def scan_text(path, text, allow):
    hits = []
    for name, pat in PATTERNS:
        for m in pat.finditer(text):
            if not plausible(name, m.group(0)):
                continue
            line = text.count("\n", 0, m.start()) + 1
            snippet = text.splitlines()[line - 1].strip()[:110] if text.splitlines() else ""
            hits.append({"rule": name, "path": path, "line": line,
                         "text": m.group(0)[:60], "snippet": snippet,
                         "allowed": is_allowed(path, name, allow)})
    return hits


def scan_worktree(allow):
    files = [f for f in tracked_files() if os.path.normpath(f) != os.path.normpath(SELF)]
    hits, skipped = [], 0
    for rel in files:
        path = os.path.join(ROOT, rel)
        if os.path.splitext(rel)[1].lower() in SKIP_EXT or not os.path.isfile(path):
            skipped += 1
            continue
        if os.path.getsize(path) > MAX_BYTES:
            skipped += 1
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            skipped += 1
            continue
        hits += scan_text(rel, text, allow)
    return files, hits, skipped


def batch_blobs(shas):
    """一次进程读多个对象：yield (sha, type, size, data)。"""
    proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    payload = ("\n".join(shas) + "\n").encode()
    out, _ = proc.communicate(payload)
    i = 0
    while i < len(out):
        nl = out.find(b"\n", i)
        if nl < 0:
            break
        header = out[i:nl].decode("utf-8", "replace").split()
        i = nl + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        sha, typ, size = header[0], header[1], int(header[2])
        body = out[i:i + size]
        i += size + 1                       # 内容后面跟一个换行
        yield sha, typ, size, body


def scan_history(allow):
    """扫全部历史 blob。路径取该 blob 第一次出现时的名字（后续改名不影响判断）。"""
    listing = decode(sh("git", "rev-list", "--objects", "--all"))
    path_of, order = {}, []
    for ln in listing.splitlines():
        parts = ln.split(" ", 1)
        sha = parts[0]
        if sha not in path_of:
            path_of[sha] = parts[1] if len(parts) > 1 else "(无名)"
            order.append(sha)

    hits, scanned = [], 0
    BATCH = 500
    for start in range(0, len(order), BATCH):
        chunk = order[start:start + BATCH]
        for sha, typ, size, body in batch_blobs(chunk):
            if typ != "blob" or size > MAX_BYTES:
                continue
            path = path_of.get(sha, "(无名)")
            if os.path.splitext(path)[1].lower() in SKIP_EXT:
                continue
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                continue                    # 二进制不按文本判
            scanned += 1
            for h in scan_text(path, text, allow):
                h["sha"] = sha[:10]
                hits.append(h)
    return scanned, hits


def report(title, hits):
    bad = [h for h in hits if not h["allowed"]]
    ok = [h for h in hits if h["allowed"]]
    print("\n%s：未豁免 %d 处，已豁免 %d 处" % (title, len(bad), len(ok)))
    for h in bad[:120]:
        print("  [%s] %s:%d  %s" % (h["rule"], h["path"], h["line"], h.get("snippet") or h["text"]))
    if len(bad) > 120:
        print("  …还有 %d 处未列出" % (len(bad) - 120))
    for h in ok[:8]:
        print("  （豁免）[%s] %s:%d" % (h["rule"], h["path"], h["line"]))
    if len(ok) > 8:
        print("  …另有 %d 处豁免未列出" % (len(ok) - 8))
    return bad


def main(argv):
    mode = "worktree"
    strict = "--strict" in argv
    if "--all" in argv:
        mode = "all"
    elif "--history" in argv:
        mode = "history"

    allow = load_allow()
    print("豁免清单：%d 条（%s）" % (len(allow), os.path.relpath(ALLOW_FILE, ROOT)))

    failed = False
    if mode in ("worktree", "all"):
        files, hits, skipped = scan_worktree(allow)
        print("工作区：%d 个文件（跳过二进制/超大 %d）" % (len(files), skipped))
        failed = bool(report("工作区", hits))

    if mode in ("history", "all"):
        scanned, hits = scan_history(allow)
        print("\n历史：%d 个文本 blob" % scanned)
        bad = report("历史遗留", hits)
        if bad and strict:
            failed = True
        elif bad:
            print("  ↑ 历史里的东西删不掉，只能重写历史或换仓库；已报告但不判失败（--strict 可改为失败）")

    if not failed:
        print("\n结果：PASS")
        return 0
    print("\n结果：FAIL — 有未豁免命中，先处理或补豁免理由")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
