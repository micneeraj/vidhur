"""Join rendered scenes with crossfades, lay voice lines, write SRT/ASS, burn subtitles.
Run from repo root: python hf_scenes/assemble.py"""
import json, os, re, subprocess, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(ROOT, "..", "zetaris_video")
VOICE = os.path.join(OUTD, "voice")
REN = os.path.join(ROOT, "renders")
X = 0.4  # crossfade length
LINES = dict(l.strip().split("|", 1) for l in open(os.path.join(ROOT, "lines.txt"), encoding="utf-8"))
W_STARTS = [1.2, 11.94, 21.27, 32.08, 43.16, 52.32]

SEQ = [("s1", [("01", 4.0), ("02", 11.58)]), ("s2", [("03", .2)]),
       ("l04", [("04", .15)]), ("l05", [("05", .15)]), ("l06", [("06", .15)]), ("l07", [("07", .15)]),
       ("l08", [("08", .15)]), ("l09", [("09", .15)]), ("s3", [("10", .15)]), ("l10", []),
       ("l11", [("11", .15)]), ("s4", [(f"W{i+1}", s) for i, s in enumerate(W_STARTS)]), ("s5", [("12", .3)])]


def dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                capture_output=True, text=True).stdout)


def ts(t, ass=False):
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}" if ass else f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"


def chunks(text, n=64):
    parts = re.split(r"(?<=[,.])\s+", text)
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) + 1 > n:
            out.append(cur); cur = p
        else:
            cur = (cur + " " + p).strip()
    out.append(cur)
    return out


def main():
    files = [os.path.join(REN, f"{s}.mp4") for s, _ in SEQ]
    D = [dur(f) for f in files]
    starts, t = [], 0.0
    for i, d in enumerate(D):
        starts.append(t); t += d - X
    total = t + X
    # video xfade chain
    fc, prev = [], "[0:v]"
    off = 0.0
    for i in range(1, len(files)):
        off += D[i - 1] - X
        lab = f"[x{i}]"
        fc.append(f"{prev}[{i}:v]xfade=transition=fade:duration={X}:offset={off:.3f}{lab}")
        prev = lab
    # voices
    vin, events, ai = [], [], len(files)
    for (s, lines), st in zip(SEQ, starts):
        for k, o in lines:
            p = os.path.join(VOICE, f"{k}.mp3"); a = st + o
            vin.append(p); events.append((k, a, a + dur(p)))
            fc.append(f"[{ai}:a]adelay={int(a*1000)}:all=1[a{ai}]"); ai += 1
    na = len(vin)
    fc.append("".join(f"[a{len(files)+j}]" for j in range(na)) + f"amix=inputs={na}:normalize=0,apad,atrim=0:{total:.3f}[aout]")
    # subtitles
    srt, ass = [], []
    n = 1
    for k, a, b in events:
        cs = chunks(LINES[k]); L = sum(len(c) for c in cs); t0 = a
        for c in cs:
            t1 = t0 + (b - a) * len(c) / L
            srt.append(f"{n}\n{ts(t0)} --> {ts(t1)}\n{c}\n"); n += 1
            ass.append(f"Dialogue: 0,{ts(t0,True)},{ts(t1,True)},Sub,,0,0,0,,{c}")
            t0 = t1
    open(os.path.join(OUTD, "subtitles.srt"), "w", encoding="utf-8").write("\n".join(srt))
    assp = os.path.join(ROOT, "renders", "subs.ass")
    open(assp, "w", encoding="utf-8").write(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Sub,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,0,0,0,0,100,100,0,0,1,2.2,1.5,2,200,200,98,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + "\n".join(ass) + "\n")
    fc.append(f"{prev}subtitles=renders/subs.ass:fontsdir=C\\\\:/Windows/Fonts,format=yuv420p[vout]")
    ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    out = os.path.join(OUTD, f"VIDHUR_{ist:%d%m%y}.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for f in files + vin:
        cmd += ["-i", f]
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "18", "-r", "30", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, cwd=ROOT)
    s4 = starts[11]
    json.dump({"out": out, "total": total, "starts": dict(zip([s for s, _ in SEQ], starts)),
               "wow_segment": [s4, s4 + D[11]], "wow_voice": [events[-7][1], events[-2][2]]},
              open(os.path.join(REN, "timeline.json"), "w"), indent=1)
    print(out, round(total, 2), "WOW scene", round(D[11], 2), "WOW voice", round(events[-2][2] - events[-7][1], 2))


if __name__ == "__main__":
    main()
