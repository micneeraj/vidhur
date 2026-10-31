import asyncio, sys, subprocess, edge_tts
OUT = "zetaris_video/voice"
async def one(k, t, rate):
    for v in ("en-IN-PrabhatNeural", "en-GB-RyanNeural"):
        try:
            await edge_tts.Communicate(t, v, rate=rate).save(f"{OUT}/{k}.mp3"); return v
        except Exception as e: print("fallback", k, e)
async def main():
    wrate = sys.argv[1] if len(sys.argv) > 1 else "+0%"
    for line in open("hf_scenes/lines.txt", encoding="utf-8"):
        k, t = line.strip().split("|", 1)
        if len(sys.argv) > 2 and not k.startswith("W"): continue
        v = await one(k, t, wrate if k.startswith("W") else "+0%")
        d = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",f"{OUT}/{k}.mp3"],capture_output=True,text=True).stdout.strip()
        print(k, v, d)
asyncio.run(main())
