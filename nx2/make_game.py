"""A game folder from the user's own dump: what the builder app (builder/) runs.

    python -I nx2/make_game.py --nup <file.nup> --out <folder> [--work <folder>] [--jobs <n>]
    python -I nx2/make_game.py --romfs <dir> --exefs <dir> --out <folder> [--work <folder>] [--jobs <n>]
    python -I nx2/make_game.py --check --nup <file.nup>       (or --exefs <dir>: is this the right game and version?)

The folder holds the executable and all it needs, started with a double click: nothing goes back into the repository, and no game
data ever comes with the tools. Lines the app reads, each on its own line of stdout:

    @@stage <id> <text>      a step begins
    @@progress <0..1>        how far that step is
    @@check <ok|bad> <text>  (--check) the answer
    @@done <folder>          finished
    @@error <text>           stopped
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = [sys.executable, "-I"]
BUILD_ID = "e0f3ccfa"   # (the version the patches, the hot list and the profile were made from)
LINUX = os.name != "nt"
BESIDE = ["hookshot", "hookshot_vk.so", "libSDL2-2.0.so.0"] if LINUX else ["hookshot.exe", "hookshot_vk.dll", "SDL2.dll", "wgpu_native.dll"]
LANGUAGES = [("en-US", "English (US)"), ("en-GB", "English (UK)"), ("fr", "French (France)"), ("fr-CA", "French (Canada)"), ("de", "German"),
             ("it", "Italian"), ("es", "Spanish (Spain)"), ("es-419", "Spanish (Latin America)"), ("pt-BR", "Portuguese (Brazil)"), ("nl", "Dutch"),
             ("pl", "Polish"), ("ru", "Russian"), ("ja", "Japanese"), ("ko", "Korean"), ("zh-Hans", "Chinese (Simplified)"),
             ("zh-Hant", "Chinese (Traditional)")]


def say(*words):
    print("@@" + " ".join(str(w) for w in words), flush=True)


def fail(text):
    say("error", text)
    sys.exit(1)


def run(cmd, env=None, on_line=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.update(env or {})
    p = subprocess.Popen(cmd, cwd=ROOT, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", bufsize=1)
    tail = []
    for line in p.stdout:
        line = line.rstrip()
        print(line, flush=True)
        tail = (tail + [line])[-12:]
        if on_line:
            on_line(line)
    if p.wait():
        why = [l for l in tail if "error:" in l.lower() and "CalledProcessError" not in l]
        fail("%s stopped: %s" % (os.path.basename(cmd[2]), why[-1] if why else tail[-1] if tail else "no output"))


def build_id(main):
    """the module id of an NSO, plain or compressed: the header is the same"""
    with open(main, "rb") as f:
        head = f.read(0x60)
    if head[:4] != b"NSO0":
        fail("%s is not a game executable" % main)
    return head[0x40:0x60].hex()


def check_modules(exefs):
    """Every module of the exefs is one the tools can read: said here, not as a Python error from the translator"""
    for name in os.listdir(exefs):
        if name != "main.npdm" and not name.endswith(".part"):
            with open(os.path.join(exefs, name), "rb") as f:
                head = f.read(16)
            if head[:4] != b"NSO0":
                fail("The file %s in this dump is not a game module (it starts %s). Try another dump of the game, or send this message to the "
                     "Hookshot developers." % (name, head.hex()))


def check(a, work):
    check_windows_sdk()   # (first: said in plain words when the game is chosen, not after it has been read)
    if a.nup:
        probe = os.path.join(work, "probe")
        shutil.rmtree(probe, ignore_errors=True)
        run(PY + ["nx2/nup.py", a.nup, "--extract", probe, "--only", "exefs"])
        check_modules(os.path.join(probe, "exefs"))
        bid = build_id(os.path.join(probe, "exefs", "main"))
        shutil.rmtree(probe, ignore_errors=True)
    else:
        bid = build_id(os.path.join(a.exefs, "main"))
    if bid.startswith(BUILD_ID):
        say("check", "ok", "This is the version Hookshot supports (build %s)." % bid[:8])
    else:
        say("check", "bad", "This is build %s. Hookshot supports build %s." % (bid[:8], BUILD_ID))


def visual_studio_environment():
    """What Visual Studio's own vcvars64.bat sets, for a compiler that does not find it by itself; None without one"""
    vswhere = os.path.join(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"), "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if not os.path.exists(vswhere):
        return None
    r = subprocess.run([vswhere, "-latest", "-prerelease", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                        "-property", "installationPath"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = r.stdout.strip().splitlines()
    if not lines:
        return None
    bat = os.path.join(lines[-1], "VC", "Auxiliary", "Build", "vcvars64.bat")
    if not os.path.exists(bat):
        return None
    r = subprocess.run('"%s" >nul && set' % bat, shell=True, capture_output=True, text=True, encoding="mbcs", errors="replace")
    keep = ("INCLUDE", "LIB", "LIBPATH", "PATH", "VCINSTALLDIR", "VCTOOLSINSTALLDIR", "VCTOOLSVERSION", "WINDOWSSDKDIR", "WINDOWSSDKVERSION",
            "UCRTVERSION", "UNIVERSALCRTSDKDIR")
    found = {}
    for line in r.stdout.splitlines():
        name, eq, value = line.partition("=")
        if eq and name.upper() in keep:
            found[name] = value
    return found if any(k.upper() == "INCLUDE" for k in found) else None


def compile_test(toolchain):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.c")
        with open(src, "w") as f:
            f.write(chr(10).join(("#include <stdio.h>", "#include <windows.h>", "int main(void) { return 0; }", "")))
        return subprocess.run([toolchain.need(toolchain.find("clang"))] + toolchain.native_target() + ["-c", src, "-o", os.path.join(d, "t.o")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")


def check_cpp_library(toolchain):
    """The renderer needs C++20's formatting from Visual Studio's library: said now, not after 300 failed files (issue 39)"""
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.cpp")
        with open(src, "w") as f:
            f.write(chr(10).join(("#include <format>", "#include <string>",
                                  "std::string t(int v) { return std::vformat(\"{}\", std::make_format_args(v)); }", "")))
        r = subprocess.run([toolchain.need(toolchain.find("clang++"))] + toolchain.native_target() + ["-std=c++20", "-c", src, "-o", os.path.join(d, "t.o")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(r.stdout + r.stderr, flush=True)
        fail("Your Visual Studio is too old for this game's renderer. Open the Visual Studio Installer, press Update on "
             "Visual Studio 2022 (or install it), then build again.")


def check_linux_compiler():
    """(linux) A C compiler that builds and links a program here (the AppImage's own clang and sysroot, else the system's):
    found now, not 40 minutes in"""
    sys.path.insert(0, os.path.join(ROOT, "third_party", "a64-kit", "tools"))
    import toolchain
    cc = toolchain.find("clang")
    if not os.path.isfile(cc):
        fail("No C compiler was found. Install clang (and lld), or use the Hookshot AppImage, which brings its own.")
    sysroot = os.environ.get("HOOKSHOT_SYSROOT")
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.c")
        with open(src, "w") as f:
            f.write(chr(10).join(("#include <stdio.h>", "#include <pthread.h>", "int main(void) { return 0; }", "")))
        r = subprocess.run([cc] + (["--sysroot=" + sysroot] if sysroot else []) + ["-fuse-ld=lld", src, "-o", os.path.join(d, "t")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(r.stdout + r.stderr, flush=True)
        errors = [l for l in r.stderr.splitlines() if "error" in l]
        fail("The compiler could not build a test program: %s (install clang, lld and your system's C library headers: "
             "libc6-dev on Debian and Ubuntu, glibc-devel on Fedora)" % (errors[0] if errors else "no message"))


def check_windows_sdk():
    """The compiler is bundled; Microsoft's C library and Windows SDK may not be, by their licences. Found now, not 40 minutes in."""
    if LINUX:
        return check_linux_compiler()
    sys.path.insert(0, os.path.join(ROOT, "third_party", "a64-kit", "tools"))
    import toolchain
    r = compile_test(toolchain)
    if r.returncode:   # (clang looks for Visual Studio its own way, and does not always find it: then ask Visual Studio)
        found = visual_studio_environment()
        if found:
            print("the compiler did not find Visual Studio by itself: using vcvars64", flush=True)
            os.environ.update(found)
            r = compile_test(toolchain)
    if not r.returncode:
        check_cpp_library(toolchain)
        return
    print(r.stdout + r.stderr, flush=True)   # (all of it, for Show details)
    errors = [l for l in r.stderr.splitlines() if "error:" in l]
    why = errors[0].split("error:", 1)[1].strip() if errors else (r.stderr.strip().splitlines() or ["no message"])[-1]
    if "windows.h" in why.lower():
        fail("The Windows SDK was not found. In the Visual Studio Installer choose Modify, and under \"Desktop development with C++\" tick a "
             "Windows 11 SDK, then build again. (%s)" % why)
    if "file not found" in why:
        fail("Visual Studio's C++ tools were not found. In the Visual Studio Installer choose Modify, tick \"Desktop development with C++\" "
             "and its MSVC x64/x86 build tools, then build again. (%s)" % why)
    fail("The compiler could not run a test build: %s" % why)


def profile_readable(prof):
    """(linux) the profile is made by a recent clang (the Windows tools'): an older one, the system's, cannot read it and stops the
    build. Then the game is built without it (a little slower in play), not at all"""
    if not LINUX:
        return True
    sys.path.insert(0, os.path.join(ROOT, "third_party", "a64-kit", "tools"))
    import toolchain
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.c")
        with open(src, "w") as f:
            f.write("int f(int x) { return x + 1; }" + chr(10))
        r = subprocess.run([toolchain.find("clang"), "-O2", "-Wno-everything", "-fprofile-instr-use=" + prof, "-c", src, "-o",
                            os.path.join(d, "t.o")], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print("this compiler cannot read the play profile (%s): building without it" % (r.stderr.strip().splitlines() or ["?"])[-1],
              flush=True)
    return r.returncode == 0


def link_or_copy(src, dst):
    """the romfs by hard links where the disk allows: 26 GB not written twice"""
    files = [os.path.join(d, f) for d, _, fs in os.walk(src) for f in fs]
    for i, s in enumerate(files):
        t = os.path.join(dst, os.path.relpath(s, src))
        os.makedirs(os.path.dirname(t), exist_ok=True)
        if not os.path.exists(t):
            try:
                os.link(s, t)
            except OSError:
                shutil.copy2(s, t)
        if i % 500 == 0:
            say("progress", "%.3f" % (i / max(1, len(files))))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--nup")
    p.add_argument("--romfs")
    p.add_argument("--exefs")
    p.add_argument("--out")
    p.add_argument("--work", default=os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "hookshot-builder")
                   if LINUX else os.path.join(os.environ.get("LOCALAPPDATA", ROOT), "HookshotBuilder"))
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    p.add_argument("--language", default="en-US", help="the game's language: a code from LANGUAGES, written to language.txt")
    p.add_argument("--check", action="store_true")
    a = p.parse_args()
    os.makedirs(a.work, exist_ok=True)
    if a.check:
        return check(a, a.work)
    if not a.out or not (a.nup or (a.romfs and a.exefs)):
        fail("needs --out, and --nup or --romfs with --exefs")
    out = os.path.abspath(a.out)
    work = os.path.abspath(a.work)
    os.makedirs(out, exist_ok=True)
    check_windows_sdk()
    exefs = os.path.join(work, "exefs")
    romfs = os.path.join(out, "romfs")

    say("stage", "read", "Reading your game files")
    shutil.rmtree(exefs, ignore_errors=True)
    if a.nup:
        # (what an earlier build left is not kept without a reason to trust it: builders before the compressed .nup fix wrote those files as
        # they lay, so a folder they left held broken game data, and the game crashed at start over its empty dictionaries)
        game = os.path.join(out, ".game")
        good = os.path.join(out, ".hookshot_romfs")
        kept = os.path.join(out, ".hookshot_exefs")
        # (an update of the builder over the same .nup: nothing to read again, only what the build needs. Issues 21 and 28)
        st = os.stat(a.nup)
        stamp = "unpacked by this builder: %s %d" % (os.path.basename(a.nup), st.st_size)
        same = False
        if os.path.exists(good) and os.path.isdir(romfs) and os.path.isdir(kept):
            with open(good) as f:
                same = f.read().strip() == stamp
        if same:
            print("the game was read from this .nup before: reading it again is skipped", flush=True)
            shutil.copytree(kept, exefs)
        else:
            shutil.rmtree(game, ignore_errors=True)
            shutil.rmtree(kept, ignore_errors=True)
            if not os.path.exists(good):
                shutil.rmtree(romfs, ignore_errors=True)
            run(PY + ["nx2/nup.py", a.nup, "--extract", game])
            shutil.move(os.path.join(game, "exefs"), exefs)
            if not os.path.isdir(romfs):
                shutil.move(os.path.join(game, "romfs"), romfs)
            shutil.rmtree(game, ignore_errors=True)
            shutil.copytree(exefs, kept)
            with open(good, "w") as f:
                f.write(stamp + chr(10))
        check_modules(exefs)
    else:
        shutil.copytree(a.exefs, exefs)   # (unpacked in place: the user's copy stays as it is)
        run(PY + ["nx2/unpack.py", exefs])
        link_or_copy(a.romfs, romfs)
    bid = build_id(os.path.join(exefs, "main"))
    if not bid.startswith(BUILD_ID):
        fail("This is build %s. Hookshot supports build %s." % (bid[:8], BUILD_ID))

    # (made once on the development PC: lists of addresses and counts, no game code)
    for name in ("hot_funcs.txt", "hot_order.txt", "hookshot_pieces.prof", "hookshot.res"):
        src = os.path.join(ROOT, "work", name)
        dst = os.path.join(work, name)
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy2(src, dst)
    os.makedirs(os.path.join(work, "image"), exist_ok=True)
    src = os.path.join(ROOT, "work", "image", "chunks.txt")   # (where the lifted code's files begin: addresses)
    dst = os.path.join(work, "image", "chunks.txt")
    if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)
    env = {"OOT_WORK": work, "OOT_EXEFS": exefs, "OOT_ROMFS": romfs, "OOT_DUMP": out, "RX_RELEASE": "1", "A64_HOT_CHUNK": "1"}
    if os.path.exists(os.path.join(work, "hot_funcs.txt")):
        env["A64_HOT_FUNCS"] = os.path.join(work, "hot_funcs.txt")
    if os.path.exists(os.path.join(work, "hookshot_pieces.prof")) and profile_readable(os.path.join(work, "hookshot_pieces.prof")):
        env["RX_PGO"] = os.path.join(work, "hookshot_pieces.prof")

    say("stage", "lift", "Translating the game to PC code")
    run(PY + ["nx2/lift.py", "--standalone", "--write", "--exefs", exefs, "--work", work], env)
    run(PY + ["nx2/split_hot.py", os.path.join(work, "sa", "gen")], env)   # (eight files side by side: ~6 minutes, not 79)

    say("stage", "compile", "Compiling")
    done = {"n": 0, "of": 1}

    def compiled(line):
        m = re.match(r"(\d+) files, ", line)
        if m:
            done["of"] = int(m.group(1))
        elif re.match(r"\s+\S+\s+\d+ MB\s+\d+s", line):
            done["n"] += 1
            say("progress", "%.3f" % (done["n"] / done["of"]))
    run(PY + ["nx2/build.py", "--standalone", "--jobs", str(a.jobs)], env, compiled)

    say("stage", "finish", "Putting the game folder together")
    built = os.path.join(work, "sa", "build")
    for name in BESIDE:
        if os.path.exists(os.path.join(built, name)):
            if os.path.exists(os.path.join(out, name)):   # (linux: not over a running copy's file: a new one in its place)
                os.remove(os.path.join(out, name))
            shutil.copy2(os.path.join(built, name), os.path.join(out, name))
    shutil.copy2(os.path.join(work, "sa", "image", "image.bin"), os.path.join(out, "image.bin"))
    # every shader of the game, for its first start to translate (gpu/vk/vk_shader.cpp seed_open). Without it the game still runs, compiling them
    # as it meets them: not worth stopping a finished build for
    if subprocess.call([sys.executable, "-I", os.path.join(HERE, "shader_census.py"), romfs, "--out", os.path.join(out, "hookshot_shaders.bin")],
                       stdout=sys.stderr):
        print("the shader list was not made: the game will compile its shaders as it plays", flush=True)
    language = os.path.join(out, "language.txt")   # (the same text as host/nn_applet.c writes)
    with open(language, "w", encoding="utf-8", newline="\n") as f:
        f.write("# The game's language. Change the line below to one of the codes listed here, save, and start the game again.\n\n%s\n\n" % a.language)
        f.writelines("# %-8s %s\n" % l for l in LANGUAGES)
    say("done", out)


if __name__ == "__main__":
    main()
