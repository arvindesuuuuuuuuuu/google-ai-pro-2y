"""setup_env_frida16.py -- One-click environment setup (Python + frida + adb + emulator)

Performs four steps:
  1. Check Python version
  2. Install frida 16.5.9 (required for script injection; 17.x removed the built-in Java bridge)
  3. Locate adb: env var -> PATH -> MuMu bundled -> auto-download platform-tools if missing
  4. Connect emulator and run `ultra0.py --check`

Usage: double-click windows/frida16/0_check_env.bat or run `python setup_env_frida16.py`
"""
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
ADB_CANDIDATES = [
    os.environ.get("ADB_PATH", ""),
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
    r"C:\Program Files (x86)\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"D:\Program Files\Netease\MuMu\nx_main\adb.exe",
    r"D:\Program Files\Netease\MuMu\nx_device\12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMu\nx_main\adb.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
    os.path.expandvars(r"%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
]
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"


def step(msg):
    print(f"\n▶ {msg}", flush=True)


def ok(msg):
    print(f"   ✅ {msg}", flush=True)


def warn(msg):
    print(f"   ⚠️  {msg}", flush=True)


def die(msg):
    print(f"\n❌ {msg}", flush=True)
    sys.exit(1)


def check_python():
    step("Checking Python")
    v = sys.version_info
    print(f"   {platform.python_version()}  ({platform.architecture()[0]})")
    if v < (3, 9):
        die("Python version too old; install 3.10 or higher: https://www.python.org/downloads/")
    ok("Python OK")


def install_frida():
    step("Installing frida 16.5.9 (required for injection)")
    try:
        import frida
        if frida.__version__.startswith("16."):
            ok(f"frida {frida.__version__} already installed")
            return
        warn(f"Found frida {frida.__version__}; 17.x is incompatible here, downgrading...")
    except Exception:
        pass

    cmds = [
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "frida==16.5.9"],
        [sys.executable, "-m", "pip", "install", "--no-cache-dir",
         "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "frida==16.5.9"],
    ]
    for c in cmds:
        print("   " + " ".join(c[2:]))
        r = subprocess.run(c, capture_output=True, text=True, errors="ignore")
        if r.returncode == 0:
            break
        print("   " + (r.stderr or r.stdout).strip().splitlines()[-1][:160])
    try:
        import importlib
        import frida
        importlib.reload(frida)
        ok(f"frida {frida.__version__} installed")
    except Exception as e:
        die(f"frida installation failed: {e}\n   Run manually: pip install frida==16.5.9")


def download_platform_tools():
    dest = BASE / "platform-tools"
    print(f"   Downloading platform-tools to {dest}")
    tmp = Path(tempfile.gettempdir()) / "platform-tools.zip"
    with urllib.request.urlopen(PLATFORM_TOOLS_URL, timeout=180) as r, open(tmp, "wb") as f:
        f.write(r.read())
    with zipfile.ZipFile(tmp) as z:
        z.extractall(BASE)
    tmp.unlink(missing_ok=True)
    adb = dest / "adb.exe"
    if adb.exists():
        ok(f"platform-tools ready: {adb}")
        return str(adb)
    die("adb.exe not found after extracting platform-tools")


def find_adb():
    step("Locating adb")
    for c in ADB_CANDIDATES:
        if not c:
            continue
        if c in ("adb", "adb.exe"):
            continue
        if Path(c).exists():
            ok(f"Using {c}")
            return c
    w = shutil.which("adb")
    if w:
        ok(f"Using {w} from PATH")
        return w
    warn("adb not found; downloading automatically")
    return download_platform_tools()


def connect_emulator(adb):
    step("Connecting emulator")
    ports = ["16384", "7555", "16416", "21503", "62001"]
    for p in ports:
        subprocess.run([adb, "connect", f"127.0.0.1:{p}"], capture_output=True, text=True, errors="ignore")
    out = subprocess.run([adb, "devices"], capture_output=True, text=True, errors="ignore").stdout
    devices = [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]
    if not devices:
        warn("No devices connected. Confirm MuMu is running and ADB debugging is enabled in settings")
        print("   " + out.strip().replace("\n", "\n   "))
        return None
    ok(f"Connected: {', '.join(devices)}")
    return devices[0]


def run_check(adb, serial):
    step("Running environment self-check (ultra0.py --check)")
    env = dict(os.environ)
    env["ADB_PATH"] = adb
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "ultra0.py", "--check"], cwd=str(BASE),
                       env=env, capture_output=True, encoding="utf-8", errors="ignore")
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode == 0


def main():
    print("=" * 60)
    print("  Google Ultra / Pro One-Click Setup")
    print("=" * 60)
    check_python()
    install_frida()
    adb = find_adb()
    serial = connect_emulator(adb)

    if serial:
        good = run_check(adb, serial)
        print("\n" + "=" * 60)
        if good:
            print("  ✅ Environment ready; run 2_Pro_12m_step1.bat to begin")
        else:
            print("  ⚠️  Self-check reported warnings; check output above (often Play Store is not running)")
        print("=" * 60)
    else:
        print("\nAfter starting MuMu and enabling ADB debugging, run this script again.")


if __name__ == "__main__":
    main()
