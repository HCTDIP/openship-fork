"""发布前闸门：不满足就 exit 1，不许上传。用法：python scripts/prepublish_check.py
条件（用户 2026-09-29 指令）：
  1) pyproject.toml 有 [project.urls] Source（GitHub 仓库地址）
  2) 发布人已轮换 PyPI token：环境变量 JEVAUDIT_TOKEN_ROTATED=yes（人工确认，脚本无法替你验证）
  3) dist/ 里的 wheel/sdist 不含任何密钥样式字符串，不含 tests/
"""
import glob, os, re, sys, tarfile, zipfile
try:
    import tomllib
except ImportError:  # py<3.11
    import tomli as tomllib

fails = []
meta = tomllib.load(open("pyproject.toml", "rb"))["project"]
src = (meta.get("urls") or {}).get("Source", "")
if not re.match(r"https://github\.com/[\w.-]+/[\w.-]+/?$", src):
    fails.append("[project.urls] Source 缺失或不是 GitHub 仓库地址")
if os.environ.get("JEVAUDIT_TOKEN_ROTATED") != "yes":
    fails.append("未确认 PyPI token 已轮换（设 JEVAUDIT_TOKEN_ROTATED=yes）")
SECRET = re.compile(rb"(pypi-AgE[\w-]{20,}|sk-or-v1-[0-9a-f]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[\w]{20,})")
dists = glob.glob("dist/*.whl") + glob.glob("dist/*.tar.gz")
if not dists:
    fails.append("dist/ 为空，先 python -m build")
for d in dists:
    if d.endswith(".whl"):
        z = zipfile.ZipFile(d); items = [(n, z.read(n)) for n in z.namelist()]
    else:
        t = tarfile.open(d); items = [(m.name, t.extractfile(m).read()) for m in t.getmembers() if m.isfile()]
    for n, b in items:
        if SECRET.search(b):
            fails.append(f"{d}: {n} 含疑似密钥")
        if d.endswith(".whl") and ("/tests/" in n or n.startswith("tests/") or "test_" in os.path.basename(n)):
            fails.append(f"{d}: wheel 里混进测试文件 {n}")
if fails:
    print("PREPUBLISH: FAIL — 不许上传"); [print("  ✗", f) for f in fails]; sys.exit(1)
print("PREPUBLISH: PASS — 可以 twine upload dist/*")
