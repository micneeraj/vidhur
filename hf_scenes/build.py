"""Cut screen clips to voice lines and generate HyperFrames panel compositions.
Run from repo root: python hf_scenes/build.py"""
import json, subprocess, os

ROOT = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(ROOT, "..", "zetaris_video")
VOICE = os.path.join(CLIPS, "voice")


def dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", p], capture_output=True, text=True).stdout)


V = {k: dur(os.path.join(VOICE, f"{k}.mp3")) for k in
     "01 02 03 04 05 06 07 08 09 10 11 W1 W2 W3 W4 W5 W6 12".split()}

# (clip, [(start, end, speed)], lower-third text or None, lower-third start)
PANELS = {
    "L04": ("c1", [(1.5, 25.6, 2.2)], None, 0),
    "L05": ("c2", [(4.5, 10.5, 3), (12, 26.2, 1)], None, 0),
    "L06": ("c3", [(1.5, 7.5, 3), (7.5, 21.6, 1)], "Live ECB API &rarr; SQL table", 2.6),
    "L07": ("c4", [(1.5, 7.5, 1.5), (9, 17.1, 1)], "Company data &times; live market data", 4.5),
    "L08": ("c6", [(2.5, 8.8, 1)], None, 0),
    "L09": ("c6", [(16.0, 22.0, 1), (31.5, 40.8, 1)], "Audit rule: 1 bad row caught", 1.0),
    "L10": ("c7", [(3, 12, 2.5), (12, 22, 1)], "Agent claim checked against Zetaris", 3.8),
    "L11": ("c7", [(22.5, 32.1, 1)], None, 0),
}
S3_LEN = 2.0
PAD = 0.7  # visual tail after each voice line


def panel_len(name):
    k = name[1:]
    d = V[k] + PAD
    return round(d - S3_LEN if k == "10" else d, 2)


def cut(name, clip, segs, target):
    out = os.path.join(ROOT, "media", f"{name}.mp4")
    src = os.path.join(CLIPS, f"{clip}.mp4")
    parts, inputs = [], []
    for i, (a, b, s) in enumerate(segs):
        parts.append(f"[0:v]trim={a}:{b},setpts=(PTS-STARTPTS)/{s},fps=30[s{i}]")
    chain = "".join(f"[s{i}]" for i in range(len(segs)))
    f = ";".join(parts) + f";{chain}concat=n={len(segs)}:v=1:a=0,scale=1920:-2," \
        f"tpad=stop_mode=clone:stop_duration=30,trim=0:{target},setpts=PTS-STARTPTS[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", f, "-map", "[v]",
                    "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", out],
                   check=True)


PANEL_HTML = open(os.path.join(ROOT, "panel_template.tpl"), encoding="utf-8").read()

if __name__ == "__main__":
    plan = {}
    for name, (clip, segs, lt, lts) in PANELS.items():
        d = panel_len(name)
        cut(name, clip, segs, d)
        html = (PANEL_HTML.replace("__ID__", name.lower()).replace("__DUR__", str(d))
                .replace("__SRC__", f"media/{name}.mp4").replace("__LT__", lt or "")
                .replace("__LTS__", str(lts)).replace("__HASLT__", "1" if lt else "0"))
        open(os.path.join(ROOT, "scenes", f"{name.lower()}.html"), "w", encoding="utf-8").write(html)
        plan[name] = d
    json.dump({"voice": V, "panels": plan, "s3": S3_LEN, "pad": PAD},
              open(os.path.join(ROOT, "plan.json"), "w"), indent=1)
    print(json.dumps(plan))
